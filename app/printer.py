"""Upload sliced print files to a local Bambu P2S; never start a print.

This module intentionally implements only authenticated FTPS login and file
transfer.  It does not contain an MQTT client or any printer-control commands.
The protocol details were checked against maintained Bambu integrations, but
the implementation here is original and uses only Python's standard library.

Certificate trust is explicit: an initial authenticated connection test returns
the SHA-256 fingerprint.  All uploads require that saved fingerprint, and it is
checked before the FTP username or password is transmitted.  The same pin is
checked on the protected data connection.  Live MQTT status is deliberately not
implemented because its certificate must be trusted independently.
"""

from __future__ import annotations

import ftplib
import hashlib
import hmac
import ipaddress
import os
import re
import socket
import ssl
import stat
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable


FTP_PORT = 990
CONNECT_TIMEOUT_SECONDS = 12
IO_TIMEOUT_SECONDS = 30
MAX_TRANSFER_SECONDS = 20 * 60
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
CHUNK_BYTES = 64 * 1024

_LAN_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)
_PLATE_FILE = re.compile(r"Metadata/plate_([1-9][0-9]*)\.gcode\Z")


class PrinterError(ValueError):
    """A bounded, user-facing printer error that never includes credentials."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "printer_error",
        certificate_fingerprint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.certificate_fingerprint = certificate_fingerprint


def _validate_host(host: str) -> str:
    if not isinstance(host, str) or "%" in host:
        raise PrinterError("Enter the printer's private IP address.", code="invalid_host")
    try:
        address = ipaddress.ip_address(host.strip())
    except ValueError as exc:
        raise PrinterError(
            "Enter a private IP address, not a hostname or URL.", code="invalid_host"
        ) from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if not any(address.version == network.version and address in network for network in _LAN_NETWORKS):
        raise PrinterError(
            "The printer must have an address on your private local network.", code="invalid_host"
        )
    return str(address)


def _validate_access_code(access_code: str) -> None:
    if (
        not isinstance(access_code, str)
        or not 1 <= len(access_code) <= 128
        or any(ord(character) < 32 or ord(character) == 127 for character in access_code)
    ):
        raise PrinterError("Enter the printer's LAN access code.", code="invalid_access_code")


def _normalize_fingerprint(value: str | None, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise PrinterError(
                "Test and save the printer connection before sending a file.", code="fingerprint_required"
            )
        return None
    if not isinstance(value, str):
        raise PrinterError("The saved printer certificate fingerprint is invalid.", code="invalid_fingerprint")
    candidate = value.strip().lower()
    if candidate.startswith("sha256:"):
        candidate = candidate[7:]
    candidate = candidate.replace(":", "")
    if not re.fullmatch(r"[0-9a-f]{64}", candidate):
        raise PrinterError("The saved printer certificate fingerprint is invalid.", code="invalid_fingerprint")
    return candidate


def _tls_context() -> ssl.SSLContext:
    # TOFU pins the exact certificate below; the local printer is not identified
    # by a public DNS certificate.  Do not share this context with general HTTP.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    return context


def _certificate_fingerprint(connection: ssl.SSLSocket) -> str:
    certificate = connection.getpeercert(binary_form=True)
    if not certificate:
        raise PrinterError("The printer did not present a TLS certificate.", code="missing_certificate")
    return hashlib.sha256(certificate).hexdigest()


def _check_fingerprint(observed: str, expected: str | None) -> None:
    if expected is not None and not hmac.compare_digest(observed, expected):
        raise PrinterError(
            "The printer's certificate has changed. No password or file was sent. "
            "Check the printer's IP address and verify its identity before trusting a new certificate.",
            code="certificate_changed",
            certificate_fingerprint=observed,
        )


class _ImplicitFTPS(ftplib.FTP_TLS):
    """An implicit-TLS FTP client with protected, session-reusing data sockets."""

    _control_fingerprint: str | None = None

    def connect(
        self,
        host: str = "",
        port: int = FTP_PORT,
        timeout: float = CONNECT_TIMEOUT_SECONDS,
        source_address=None,
    ) -> str:
        if self.sock is not None:
            self.close()
        self.host = host
        self.port = port
        self.timeout = timeout
        self.source_address = source_address
        # ftplib normally ignores a PASV response's advertised host and uses
        # the connected server.  Keep that protection explicitly enabled.
        self.trust_server_pasv_ipv4_address = False
        raw_socket = socket.create_connection((host, port), timeout, source_address)
        try:
            self.af = raw_socket.family
            self.sock = self.context.wrap_socket(raw_socket, server_hostname=host)
            self.file = self.sock.makefile("r", encoding=self.encoding)
            self.welcome = self.getresp()
            return self.welcome
        except BaseException:
            raw_socket.close()
            self.close()
            raise

    def ntransfercmd(self, cmd: str, rest=None):
        # Start with FTP's unwrapped data socket, then reuse the control TLS
        # session.  FTP_TLS's normal implementation creates a fresh session.
        data_socket, expected_size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        try:
            if not self._prot_p or not self._control_fingerprint:
                raise PrinterError("A protected file-transfer connection is required.", code="unprotected_transfer")
            data_socket = self.context.wrap_socket(
                data_socket,
                server_hostname=self.host,
                session=self.sock.session,
            )
            observed = _certificate_fingerprint(data_socket)
            if not hmac.compare_digest(observed, self._control_fingerprint):
                raise PrinterError(
                    "The file-transfer connection presented a different certificate. No file data was sent.",
                    code="data_certificate_changed",
                    certificate_fingerprint=observed,
                )
            data_socket.settimeout(IO_TIMEOUT_SECONDS)
            return data_socket, expected_size
        except BaseException:
            data_socket.close()
            raise


def _close_connection(client) -> None:
    if client is not None:
        try:
            # Closing avoids waiting for TLS shutdown on firmware that does not
            # answer close_notify.  No extra printer commands are issued here.
            client.close()
        except Exception:
            pass


def _open_connection(host: str, access_code: str, expected: str | None):
    client = _ImplicitFTPS(context=_tls_context(), timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        client.connect(host, FTP_PORT, timeout=CONNECT_TIMEOUT_SECONDS)
        observed = _certificate_fingerprint(client.sock)
        # This check must precede login: neither USER nor PASS has been sent.
        _check_fingerprint(observed, expected)
        client._control_fingerprint = observed
        client.sock.settimeout(IO_TIMEOUT_SECONDS)
        client.login(user="bblp", passwd=access_code)
        client.prot_p()
        client.set_pasv(True)
        return client, observed
    except BaseException:
        _close_connection(client)
        raise


def _friendly_error(error: Exception) -> PrinterError:
    if isinstance(error, PrinterError):
        return error
    if isinstance(error, (TimeoutError, socket.timeout)):
        return PrinterError(
            "The printer connection timed out. Check that the printer is awake and reachable on the local network.",
            code="timeout",
        )
    if isinstance(error, ssl.SSLError):
        return PrinterError("The secure connection to the printer failed.", code="tls_error")
    if isinstance(error, ftplib.error_perm):
        if str(error).startswith("530"):
            return PrinterError(
                "The printer rejected the local login. Check its IP address and access code. Use Open in Bambu Studio to keep cloud services enabled if this firmware does not allow local file transfers.",
                code="authentication_failed",
            )
        return PrinterError(
            "The printer refused the file operation. Check that a writable USB drive is connected.",
            code="ftp_rejected",
        )
    if isinstance(error, ftplib.all_errors):
        return PrinterError(
            "The printer connection or file transfer failed. Check its IP address, LAN settings and USB drive.",
            code="connection_failed",
        )
    return PrinterError("The file could not be sent to the printer.", code="transfer_failed")


def test_connection(host, access_code, expected_fingerprint=None) -> dict:
    """Authenticate without uploading, returning a lowercase SHA-256 pin.

    An omitted fingerprint is the explicit first-use trust step.  Callers should
    only save the returned pin after this function succeeds, and must not
    silently retry a changed pin with ``None``.
    """
    host = _validate_host(host)
    _validate_access_code(access_code)
    expected = _normalize_fingerprint(expected_fingerprint)
    client = None
    try:
        client, observed = _open_connection(host, access_code, expected)
        client.pwd()
        return {
            "ok": True,
            "certificate_fingerprint": observed,
            "message": "Printer login succeeded. The certificate is ready to use for file transfers.",
        }
    except Exception as exc:
        raise _friendly_error(exc) from None
    finally:
        _close_connection(client)


def _unique_remote_name(requested: str) -> str:
    if not isinstance(requested, str) or not requested.lower().endswith(".gcode.3mf"):
        raise PrinterError("Only a sliced .gcode.3mf file can be sent to the printer.", code="not_sliced_file")
    if "/" in requested or "\\" in requested or any(ord(char) < 32 for char in requested):
        raise PrinterError("The print file name must not contain a path or control characters.", code="invalid_filename")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", requested[:-10]).strip("_-")[:48] or "print"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stem}_{timestamp}_{uuid.uuid4().hex}.gcode.3mf"


def _inspect_sliced_archive(stream: BinaryIO) -> list[int]:
    try:
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise PrinterError("The print archive contains too many entries.", code="invalid_archive")
            plates = []
            for entry in entries:
                match = _PLATE_FILE.fullmatch(entry.filename)
                if match:
                    if entry.flag_bits & 1 or entry.file_size <= 0:
                        raise PrinterError("The sliced G-code is empty or encrypted.", code="invalid_archive")
                    # Read only a small prefix; no ZIP members are extracted.
                    # The slicer remains responsible for machine compatibility.
                    with archive.open(entry) as member:
                        prefix = member.read(min(4096, entry.file_size))
                    if not prefix or b"\x00" in prefix:
                        raise PrinterError("The archive does not contain readable sliced G-code.", code="invalid_archive")
                    try:
                        prefix.decode("utf-8-sig")
                    except UnicodeDecodeError as exc:
                        raise PrinterError("The sliced G-code is not a text file.", code="invalid_archive") from exc
                    plates.append(int(match.group(1)))
            if not plates or len(set(plates)) != len(plates):
                raise PrinterError(
                    "This file has no valid sliced plate. Export a plate sliced for your P2S from Bambu Studio first.",
                    code="not_sliced_file",
                )
            return sorted(plates)
    except PrinterError:
        raise
    except (zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError, ValueError, OSError) as exc:
        raise PrinterError("The file is not a readable sliced 3MF archive.", code="invalid_archive") from exc
    finally:
        stream.seek(0)


def _send_stream(client, remote_path: str, stream: BinaryIO, total: int, on_progress) -> int:
    client.voidcmd("TYPE I")
    started = time.monotonic()
    sent = 0
    data_socket = client.transfercmd(f"STOR {remote_path}")
    try:
        data_socket.settimeout(IO_TIMEOUT_SECONDS)
        if on_progress is not None:
            on_progress(0, total)
        while sent < total:
            if time.monotonic() - started > MAX_TRANSFER_SECONDS:
                raise PrinterError("The file transfer exceeded its time limit.", code="timeout")
            chunk = stream.read(min(CHUNK_BYTES, total - sent))
            if not chunk:
                raise PrinterError("The local print file changed during transfer.", code="file_changed")
            data_socket.sendall(chunk)
            sent += len(chunk)
            if on_progress is not None:
                on_progress(sent, total)
        if stream.read(1):
            raise PrinterError("The local print file changed during transfer.", code="file_changed")
    finally:
        # Deliberately no SSLSocket.unwrap(): some printer firmware never
        # completes the TLS close_notify handshake after a data transfer.
        data_socket.close()
    client.voidresp()
    return sent


def upload_file(
    host,
    access_code,
    local_path,
    remote_name,
    expected_fingerprint,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Upload a sliced archive to USB root and verify its reported size.

    The requested name is made unique; existing printer files are not replaced.
    ``on_progress`` receives ``(bytes_sent, total_bytes)``.  A successful result
    means the file was transferred, never that printing has started.  Errors
    raise :class:`PrinterError` with a safe message and a machine-readable code.
    """
    host = _validate_host(host)
    _validate_access_code(access_code)
    expected = _normalize_fingerprint(expected_fingerprint, required=True)
    generated_name = _unique_remote_name(remote_name)
    remote_path = f"/{generated_name}"
    client = None
    transfer_started = False
    try:
        with Path(local_path).open("rb") as stream:
            file_stat = os.fstat(stream.fileno())
            if not stat.S_ISREG(file_stat.st_mode) or not 0 < file_stat.st_size <= MAX_FILE_BYTES:
                raise PrinterError("The sliced file must be a regular file smaller than 512 MiB.", code="invalid_file_size")
            plates = _inspect_sliced_archive(stream)
            client, observed = _open_connection(host, access_code, expected)
            client.cwd("/")
            transfer_started = True
            sent = _send_stream(client, remote_path, stream, file_stat.st_size, on_progress)
            remote_size = client.size(remote_path)
            if remote_size is None or int(remote_size) != sent:
                raise PrinterError("The printer did not confirm the complete file size. The transfer was not accepted.", code="size_mismatch")
        return {
            "remote_path": remote_path,
            "remote_name": generated_name,
            "bytes_sent": sent,
            "certificate_fingerprint": observed,
            "plates": plates,
            "message": "File sent to the printer's USB storage. Start it from the printer screen.",
        }
    except Exception as exc:
        if transfer_started and client is not None:
            try:
                # Only this call's randomly named partial file is eligible for
                # cleanup.  Never delete an existing or caller-selected path.
                client.delete(remote_path)
            except Exception:
                pass
        raise _friendly_error(exc) from None
    finally:
        _close_connection(client)


def get_status(host, serial, access_code, expected_fingerprint=None) -> dict:
    """Return an explicit unsupported status without opening another protocol.

    An FTPS certificate pin is not evidence of the MQTT server's identity.
    Keeping status unavailable is preferable to silently trusting or reusing
    that pin.  This function performs no network requests and sends no commands.
    """
    return {
        "ok": False,
        "available": False,
        "state": "unavailable",
        "progress": None,
        "remaining_minutes": None,
        "reason": "status_not_implemented",
        "message": "Live print status is not available in this version. Send sliced files to the printer and start them from its screen.",
    }
