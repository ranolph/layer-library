# Installation and operation

## Docker Compose

Install Docker Engine or Docker Desktop with the Compose v2 command. Download or clone the complete repository and run from its root:

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail 100 layer-library
```

Open `http://localhost:8777`, or substitute the host's reachable LAN address. The health endpoint is `/api/health`; the image includes a health check. The application listens on port **8080 inside the container**, which Compose publishes as **8777** by default.

The build uses Python 3.12 and installs the pinned `requirements.txt`. The checked-in viewer bundle is ready to serve. Node.js is only needed when changing that viewer.

The container runs as UID/GID **568:568**, with a read-only root filesystem, a temporary `/tmp`, and writable persistent storage at `/data`. A new named volume inherits the prepared `/data` ownership from the image. If you replace it with a host bind mount, grant that UID/GID access to the selected data directory.

The entrypoint creates `/data/incoming` and sets `TMPDIR` there before Python starts. Large multipart uploads therefore spool to the data disk rather than the small RAM-backed `/tmp`. Keep enough free data-disk space for temporary uploads as well as the originals and Studio conversion workspaces.

### Change ports or volume name

Copy `.env.example` to `.env` and edit it before starting:

```dotenv
LAYER_LIBRARY_BIND=0.0.0.0
LAYER_LIBRARY_PORT=8777
LAYER_LIBRARY_VOLUME=layer-library-data
MAX_UPLOAD_MB=250
BACKUP_SERVICE_ORIGIN=
```

These are instance settings; do not commit a populated `.env`. Give each independent instance its own volume name and host port. Keep the chosen volume name unchanged during upgrades so the replacement container uses the same library.

## TrueNAS

Use a Docker-based TrueNAS Apps installation. TrueNAS CORE and older Kubernetes-based Apps installations do not use this Compose deployment directly. The [official TrueNAS Custom Apps guide](https://apps.truenas.com/managing-apps/installing-custom-apps/) describes the current guided installer and **Apps → Discover Apps → menu → Install via YAML** workflow.

There are two practical deployment approaches:

1. **Compose from a checked-out folder.** Put this repository in an application-source directory on a dataset, enter that directory in a shell, and run `docker compose up -d --build`. Manage that instance with the same Compose project. Its persistent named volume must be included in your backup plan.
2. **A TrueNAS Custom App using an image.** Build the image from this source first, then configure the Custom App to use that image. Map a persistent dataset into `/data` and expose container port 8080 as your chosen host port, usually 8777. Configure the process as UID/GID 568:568 and grant the dataset corresponding access.

For a local build, from the repository root on the Docker host:

```sh
docker build -t layer-library:1.0.6 .
```

If you build on a different machine, transfer the image to your Docker host or push it to a container registry you control. A locally loaded image must be configured so the app uses that local image instead of trying to pull `layer-library` from a public registry. There is no pre-published public image assumed by this repository. For a registry deployment, substitute your actual image repository and tag in the Custom App configuration.

After building on the NAS, you can use **Install via YAML** with this image-only example. First create your data dataset, grant UID/GID 568:568 write access, and replace `/mnt/your-pool/apps/layer-library-data` with its actual path:

```yaml
services:
  layer-library:
    image: layer-library:1.0.6
    pull_policy: never
    user: "568:568"
    restart: unless-stopped
    ports:
      - "8777:8080"
    environment:
      DATA_DIR: /data
      MAX_UPLOAD_MB: "250"
      BACKUP_SERVICE_ORIGIN: ""
    volumes:
      - /mnt/your-pool/apps/layer-library-data:/data
    read_only: true
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=128m,mode=1777
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
```

Docker's [`pull_policy: never`](https://docs.docker.com/reference/compose-file/services/#pull_policy) uses the image already present on that host and fails if it is absent. A registry deployment should use a suitable pull policy instead. Use only one of the Compose or Apps-managed instances on host port 8777 at a time. For later Apps upgrades, build the intended release image and update/redeploy the Custom App with that tag.

**Pasting the supplied `compose.yaml` into a remote YAML editor does not upload the repository.** Its `build: context: .` needs the source files next to the Compose project. For an Apps UI deployment, build or publish the image first and use that image in the configuration instead of the local build section.

Keep application source and persistent data in separate locations. For a host-path deployment, your chosen dataset goes to `/data`; it should contain `library.sqlite3`, `files/`, `thumbnails/`, `incoming/` and `studio_cache/` after startup. Do not put those runtime files in the GitHub repository.

## Run directly with Python

Python 3.12 is the tested target. From the repository root:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8777 --no-proxy-headers
```

This creates `./data` on first startup and exposes the app only on the local computer. To serve the trusted LAN, change the host to `0.0.0.0`. To select another storage directory, set `DATA_DIR` before launching. Windows users can activate the environment with `.venv\Scripts\Activate.ps1` and run the same Python module command.

There is no Node build step for normal use. For development tests, install `requirements-dev.txt` instead. Python module paths assume the working directory is the repository root.

## Data, backups and restore

The entire data directory is the unit of persistence:

| Location under `/data` | Contents |
| --- | --- |
| `library.sqlite3` and SQLite sidecar files | Model metadata, collections, tags, Featured flags, jobs and browser sessions. |
| `printer.json`, when configured | Optional printer connection settings and LAN access code; written with owner-only file permissions. |
| `files/` | Uploaded originals and attached sliced print files. |
| `thumbnails/` | Saved or generated preview images. |
| `studio_cache/` | Prepared geometry-only 3MF handoff files. |
| `incoming/` | Temporary upload files. |

For a consistent ordinary backup, stop the app and copy or snapshot the **whole data directory**, then start it again. A TrueNAS dataset snapshot can provide the storage copy when you use a host-path dataset. Database-only backups omit the model files.

For the default Compose volume, Docker can copy the stopped container's mounted data without requiring access to Docker's internal volume directory:

```sh
docker compose stop
docker cp "$(docker compose ps -aq layer-library)":/data ./layer-library-backup
docker compose start
```

Use a new destination name for each backup. Keep a copy outside the Docker host or NAS as appropriate. If the copy fails, start the service again and investigate before assuming a backup exists.

To restore, stop the app, restore the complete saved directory into the selected persistent volume or dataset, and ensure UID/GID 568:568 can read and write it before starting. Restore into an empty destination or a separate test instance so stale files are not merged with the backup. Test by opening several models and downloading their originals. If LAN printer access was configured, backups include the access code in `printer.json` and must be kept private.

An upgrade is a source/image replacement, not a replacement data directory:

```sh
# First make a backup as described above.
# Replace or update the source checkout to the intended release.
docker compose up -d --build
docker compose logs --tail 100 layer-library
```

Existing databases are upgraded in place as needed. The Featured column is added without resetting existing models. Before trying an older version after an upgrade, restore a matching pre-upgrade data backup into a separate instance. Do not use `docker compose down --volumes` when you intend to retain the library.

## Bambu Studio and the optional local connection

The default print workflow is **Bambu Studio / cloud account**. Install Studio on your own computer and use its normal sign-in and printer connection. The desktop app must be able to reach the library's URL. An operating-system protocol-handler prompt may appear when opening Studio. If the desktop link does not open, download the original or prepared file and use Studio's File menu.

STL/OBJ handoff creates a geometry-only 3MF without rescaling, repairing, slicing, importing external materials or modifying the original. Open and non-manifold models remain subject to any repair your slicer needs. A 3MF original is passed through unchanged. Conversion may decline unusually complex files; the original download remains available.

The optional LAN setting is for compatible Bambu P2S file transfers. It requires a private numeric printer IP address, a printer access code and a successfully tested connection. Support depends on printer firmware and local configuration. The client pins the printer's observed TLS certificate; a changed certificate requires review and another connection test. Current transfer support is for sliced P2S `.gcode.3mf` files. It copies the file to storage and never sends a print-start command. Live printer telemetry is not implemented.

## Optional backup-service integration

By default `BACKUP_SERVICE_ORIGIN` is empty and no backup button is shown. The application does not contact a backup service automatically.

If you already operate a compatible Launchpad website-backups service, set this variable to its single HTTP(S) origin, such as `https://backup.example.test:9443`. Replace that example with your actual service; do not include a path, credentials, query, fragment or trailing slash. Recreate the app after changing the variable.

The separate service must configure site ID `layer-library`, include this app's persistent data as a source, allow the library's browser origin, and implement the client API:

- `GET /api/status` reports whether the service and site are configured.
- `GET /api/session` returns the backup session token.
- `POST /api/backups`, with the `X-Backup-Token` header and `{"site_id":"layer-library"}`, starts or reuses a job.
- `GET /api/backups/{id}` returns progress and outcome.

Only that configured origin is added to the browser's `connect-src` policy. Google Drive authorisation, credentials, archive handling and restore operations belong to the separate service. Setting the origin alone does not install or configure it. The service is optional; standard data-directory backups need none of it.

## Limits and troubleshooting

| Symptom | Check |
| --- | --- |
| The library looks empty after an upload | Featured opens first. Choose All models, then star the models you want on the home view. |
| The container cannot write its database | Confirm the `/data` mount is writable by UID/GID 568:568 and is not a read-only mount. |
| No preview or the preview is too large | The original is still downloadable. Try Rebuild preview image for STL/OBJ. For 3MF, automatic images use an embedded picture; if none is usable, open its 3D preview to capture a browser image. Large browser previews require explicit opt-in; server rendering has separate bounded budgets. |
| OBJ looks untextured | OBJ materials and textures are intentionally not loaded; geometry is displayed in a preview colour. |
| A large 3MF is rejected | The browser caps each file at 250 MiB. `MAX_UPLOAD_MB` can lower the server limit; raising it does not raise the browser ceiling. ZIP expansion, entity, geometry and streaming XML checks also apply; the model XML limit is 512 MiB and the expanded archive limit is 800 MiB. Increasing the upload setting does not disable these checks. |
| Studio cannot download the handoff | Use a library URL reachable from the computer running Studio. Do not pass a container-only hostname to a desktop outside that network. |
| A browser operation returns a session error | Refresh the page to renew its session. Cross-origin writes are rejected. Cookies must be enabled. |
| The image will not pull in TrueNAS | Build/load it on that Docker host and use a local-image policy, or point the app to the registry where you published the image. The sample image tag is a local build name. |

The default service uses direct HTTP on a trusted network and ignores forwarded proxy headers. A reverse proxy with a different scheme/origin needs an explicit, carefully scoped trusted-proxy configuration or the browser's write-origin checks will reject it. A VPN that allows the existing LAN address is an alternative for remote access. There is no built-in public-user authentication, user separation or multi-tenant storage.
