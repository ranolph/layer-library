# Changelog

## 1.0.6 — Public source package — 2026-11-18

- Prepared a standalone, empty-library installation with portable Docker
  Compose configuration and local Python instructions.
- Preserved the latest STL, OBJ and 3MF import, thumbnail, model preview and
  Bambu Studio export workflows.
- Preserved Featured models and existing metadata, collection, print-job and
  trash workflows.
- Removed personal network addresses, backup-service defaults, seed models
  and installation-specific configuration.
- Made the optional backup-service integration explicitly configurable.
- Added MIT licensing, bundled dependency notices, tests, contribution
  guidance and GitHub Actions checks.

Existing printer transfer and print-job fit checks remain specific to the
documented Bambu P2S workflow. General model storage and downloads do not
require a printer integration.

