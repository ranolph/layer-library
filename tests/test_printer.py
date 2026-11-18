"""Network-free tests for the deliberately upload-only printer transport."""

import hashlib
import io
import ssl
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from app import printer


CERTIFICATE = b"mock-printer-certificate-DER"
PIN = hashlib.sha256(CERTIFICATE).hexdigest()
HOST = "192.168.10.42"
ACCESS_CODE = "testcode"


class FakeSocket:
    def __init__(self, certificate=CERTIFICATE):
        self.certificate = certificate
        self.session = object()
        self.closed = False

    def getpeercert(self, binary_form=False):
        return self.certificate

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True


class FakeDataSocket(FakeSocket):
    def __init__(self):
        super().__init__()
        self.content = bytearray()

    def sendall(self, content):
        self.content.extend(content)

    def unwrap(self):
        raise AssertionError("Data TLS must not be unwrapped")


class FakeFTP:
    def __init__(self, certificate=CERTIFICATE, size_delta=0):
        self.sock = FakeSocket(certificate)
        self.data_socket = FakeDataSocket()
        self.events = []
        self.size_delta = size_delta
        self.closed = False

    def connect(self, host, port, timeout):
        self.events.append(("connect", host, port, timeout))
        return "220 Ready"

    def login(self, user, passwd):
        self.events.append(("login", user))
        self.password_received = passwd

    def prot_p(self):
        self.events.append(("prot_p",))

    def set_pasv(self, enabled):
        self.events.append(("passive", enabled))

    def pwd(self):
        self.events.append(("pwd",))
        return "/"

    def cwd(self, path):
        self.events.append(("cwd", path))

    def voidcmd(self, command):
        self.events.append(("command", command))
        return "200 OK"

    def transfercmd(self, command):
        self.events.append(("transfer", command))
        return self.data_socket

    def voidresp(self):
        self.events.append(("transfer_complete",))
        return "226 Transfer complete"

    def size(self, path):
        self.events.append(("size", path))
        return len(self.data_socket.content) + self.size_delta

    def delete(self, path):
        self.events.append(("delete", path))

    def close(self):
        self.closed = True


class PrinterTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def sliced_file(self, name="input.gcode.3mf", plate=1):
        path = self.directory / name
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"Metadata/plate_{plate}.gcode", "; sliced test fixture\nG1 X10 Y10\n")
            archive.writestr("Metadata/slice_info.config", "<config/>")
        return path

    def test_first_authenticated_test_returns_pin_without_transfer(self):
        ftp = FakeFTP()
        with patch.object(printer, "_ImplicitFTPS", return_value=ftp):
            result = printer.test_connection(HOST, ACCESS_CODE)
        self.assertTrue(result["ok"])
        self.assertEqual(result["certificate_fingerprint"], PIN)
        self.assertEqual(ftp.password_received, ACCESS_CODE)
        self.assertFalse(any(event[0] == "transfer" for event in ftp.events))
        self.assertTrue(ftp.closed)

    def test_upload_rejects_changed_certificate_before_sending_password(self):
        path = self.sliced_file()
        ftp = FakeFTP(certificate=b"different certificate")
        with patch.object(printer, "_ImplicitFTPS", return_value=ftp):
            with self.assertRaises(printer.PrinterError) as caught:
                printer.upload_file(HOST, ACCESS_CODE, path, "example.gcode.3mf", PIN)
        self.assertEqual(caught.exception.code, "certificate_changed")
        self.assertFalse(hasattr(ftp, "password_received"))
        self.assertFalse(any(event[0] in {"login", "transfer", "delete"} for event in ftp.events))
        self.assertTrue(ftp.closed)

    def test_pinned_connection_test_also_refuses_changed_certificate(self):
        ftp = FakeFTP(certificate=b"different certificate")
        with patch.object(printer, "_ImplicitFTPS", return_value=ftp):
            with self.assertRaises(printer.PrinterError):
                printer.test_connection(HOST, ACCESS_CODE, PIN)
        self.assertFalse(hasattr(ftp, "password_received"))

    def test_upload_requires_saved_fingerprint_before_network_access(self):
        path = self.sliced_file()
        with patch.object(printer, "_ImplicitFTPS") as factory:
            with self.assertRaises(printer.PrinterError) as caught:
                printer.upload_file(HOST, ACCESS_CODE, path, "example.gcode.3mf", None)
        self.assertEqual(caught.exception.code, "fingerprint_required")
        factory.assert_not_called()

    def test_successful_upload_is_unique_verified_and_does_not_start_print(self):
        path = self.sliced_file(plate=2)
        ftp = FakeFTP()
        progress = []
        with patch.object(printer, "_ImplicitFTPS", return_value=ftp):
            result = printer.upload_file(
                HOST, ACCESS_CODE, path, "My pot é.gcode.3mf", PIN,
                lambda done, total: progress.append((done, total)),
            )
        self.assertRegex(result["remote_name"], r"^My_pot_\d{8}T\d{6}Z_[0-9a-f]{32}\.gcode\.3mf$")
        self.assertEqual(result["bytes_sent"], path.stat().st_size)
        self.assertEqual(result["plates"], [2])
        self.assertEqual(bytes(ftp.data_socket.content), path.read_bytes())
        self.assertEqual(progress[-1], (path.stat().st_size, path.stat().st_size))
        self.assertIn(("size", result["remote_path"]), ftp.events)
        commands = [entry[1] for entry in ftp.events if entry[0] in {"command", "transfer"}]
        self.assertEqual(commands, ["TYPE I", "STOR " + result["remote_path"]])
        self.assertTrue(ftp.data_socket.closed)
        self.assertTrue(ftp.closed)

    def test_raw_stl_and_unsliced_project_are_rejected_before_connect(self):
        stl = self.directory / "disguised.gcode.3mf"
        stl.write_bytes(b"solid pot\nendsolid pot\n")
        unsliced = self.directory / "project.gcode.3mf"
        with zipfile.ZipFile(unsliced, "w") as archive:
            archive.writestr("3D/3dmodel.model", "<model/>")
        with patch.object(printer, "_ImplicitFTPS") as factory:
            for path in (stl, unsliced):
                with self.subTest(path=path.name):
                    with self.assertRaises(printer.PrinterError):
                        printer.upload_file(HOST, ACCESS_CODE, path, path.name, PIN)
        factory.assert_not_called()

    def test_remote_size_mismatch_fails_and_cleans_only_unique_upload(self):
        path = self.sliced_file()
        ftp = FakeFTP(size_delta=-1)
        with patch.object(printer, "_ImplicitFTPS", return_value=ftp):
            with self.assertRaises(printer.PrinterError) as caught:
                printer.upload_file(HOST, ACCESS_CODE, path, "pot.gcode.3mf", PIN)
        self.assertEqual(caught.exception.code, "size_mismatch")
        uploaded_path = next(event[1][5:] for event in ftp.events if event[0] == "transfer")
        self.assertEqual([event for event in ftp.events if event[0] == "delete"], [("delete", uploaded_path)])
        self.assertRegex(uploaded_path, r"^/pot_\d{8}T\d{6}Z_[0-9a-f]{32}\.gcode\.3mf$")

    def test_data_socket_reuses_control_tls_session_and_certificate(self):
        context = Mock()
        raw_data = FakeSocket()
        protected_data = FakeSocket()
        context.wrap_socket.return_value = protected_data
        ftp = printer._ImplicitFTPS(context=context)
        ftp.host = HOST
        ftp.sock = FakeSocket()
        ftp._prot_p = True
        ftp._control_fingerprint = PIN
        with patch("ftplib.FTP.ntransfercmd", return_value=(raw_data, None)):
            result, size = ftp.ntransfercmd("STOR /fixture.gcode.3mf")
        context.wrap_socket.assert_called_once_with(raw_data, server_hostname=HOST, session=ftp.sock.session)
        self.assertIs(result, protected_data)

    def test_changed_data_certificate_closes_socket_before_file_bytes(self):
        context = Mock()
        protected_data = FakeSocket(certificate=b"unexpected data peer")
        context.wrap_socket.return_value = protected_data
        ftp = printer._ImplicitFTPS(context=context)
        ftp.host = HOST
        ftp.sock = FakeSocket()
        ftp._prot_p = True
        ftp._control_fingerprint = PIN
        with patch("ftplib.FTP.ntransfercmd", return_value=(FakeSocket(), None)):
            with self.assertRaises(printer.PrinterError) as caught:
                ftp.ntransfercmd("STOR /fixture.gcode.3mf")
        self.assertEqual(caught.exception.code, "data_certificate_changed")
        self.assertTrue(protected_data.closed)

    def test_private_numeric_hosts_and_safe_names_are_required(self):
        path = self.sliced_file()
        with patch.object(printer, "_ImplicitFTPS") as factory:
            for host in ("127.0.0.1", "8.8.8.8", "printer.local", "http://192.168.1.2", "169.254.1.2"):
                with self.subTest(host=host):
                    with self.assertRaises(printer.PrinterError):
                        printer.test_connection(host, ACCESS_CODE)
            for name in ("../pot.gcode.3mf", "pot.stl", "pot.3mf", "pot\n.gcode.3mf"):
                with self.subTest(name=name):
                    with self.assertRaises(printer.PrinterError):
                        printer.upload_file(HOST, ACCESS_CODE, path, name, PIN)
        factory.assert_not_called()

    def test_tls_is_capped_at_1_2(self):
        context = printer._tls_context()
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(context.maximum_version, ssl.TLSVersion.TLSv1_2)

    def test_status_is_explicitly_unavailable_and_sends_nothing(self):
        with patch("socket.create_connection") as connection:
            result = printer.get_status(HOST, "serial", ACCESS_CODE, PIN)
        self.assertFalse(result["available"])
        self.assertIsNone(result["progress"])
        connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
