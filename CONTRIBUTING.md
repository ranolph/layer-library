# Contributing to Layer Library

Use an issue to describe a substantial feature or behaviour change before
investing in a large patch. Small fixes can go straight into a pull request.

## Development

Follow the local installation instructions in [README.md](README.md). Keep
runtime data outside tracked source. Use synthetic or redistributable test
fixtures; do not include your personal library, printer configuration or saved
database.

Create a virtual environment and install the pinned development dependencies.

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
npm ci
npm test
npm run build
```

Commit the rebuilt `app/static/viewer.bundle.js` when changing its source or
dependencies. Preserve its legal comments. Keep existing model metadata and
print-job records compatible when changing the database or import code.

## Pull requests

Explain the problem, the resulting behaviour and how you verified the change.
Keep unrelated changes separate. Run the relevant checks and mention anything
you could not verify.

The GitHub Actions workflow also builds and starts the Docker Compose
application. A passing Python/JavaScript test suite alone does not verify the
container.

## Licensing

Contributions are provided under the repository's MIT License. Preserve
third-party copyright notices and include licence information for any new
dependency or asset.

