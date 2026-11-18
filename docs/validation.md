# Release validation

Prepared for the public Layer Library **1.0.6** source package on **18 November
2026**. The package preserves the full STL/OBJ/3MF, thumbnail, Bambu Studio and
Featured feature set from the preceding application updates.

## Checks completed

| Area | Result |
| --- | --- |
| Clean Python environment | Exact requirements-dev.txt installed successfully in a fresh Python 3.12 virtual environment. |
| Exact CI test command | `python -m pytest -q`: 133 tests and 125 subtests passed. |
| JavaScript checks | 8 OBJ/viewer and 28 Studio/frontend checks passed. |
| Frontend dependency installation | `npm ci` completed from the supplied lockfile. |
| Viewer rebuild | `npm run build` reproduced the checked-in viewer byte-for-byte. |
| Fresh installation and persistence | Empty library, configured storage, Featured state, metadata edits, trash/restore, restart and migration checks passed. |
| Multipart staging | A fresh process used the Docker entrypoint to spool an actual 2 MiB multipart upload beneath the data directory; uploaded/downloaded bytes matched and temporary files were cleaned. |
| Actual Form Studio integration | 36 checks passed using the public Form Studio 1.2.0 and Layer Library 1.0.6 servers on temporary loopback ports. |
| Public package review | Required source/assets and notices included; personal addresses, live data, uploaded models and printer credentials excluded. |

The Python test run emitted one upstream Starlette TestClient deprecation
warning. There were no failed tests. Printer network operations in the test
suite use mocks.

The viewer bundle SHA-256 is:

```text
e25af4a89cf65ad8ae0042eb5f7f1e3ac7410d31da7ef6f844e668e7f526e547
```

The companion-app test generated and transferred two actual vessel meshes,
checked original download bytes and inspection metadata, and verified session
and CSRF handling, retries, deduplication preserving custom metadata and
Featured status, recipes, saved-design recovery and oversized recipe warnings.
All model files and databases were synthetic and temporary.

## Reproduce the checks

From the repository root, in a Python 3.12 virtual environment:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
npm ci
npm test
npm run build
git diff --exit-code -- app/static/viewer.bundle.js
```

The actual companion-app integration script is supplied in Form Studio's
repository at `tests/integration_layer_library.py`. Its CONTRIBUTING.md explains
how to run it against a sibling Layer Library source checkout.

## Deployment verification still needed

A Docker daemon and a live TrueNAS instance were unavailable for these release
checks. Container builds, Docker volume ownership and TrueNAS Apps deployment
were reviewed in source but were not executed in that environment. The included
GitHub Actions workflow builds, starts and health-checks the container after the
repository is uploaded.

No physical printer transfer/print or live optional backup-service integration
was performed. Those depend on the user's hardware, firmware and external
service configuration.
