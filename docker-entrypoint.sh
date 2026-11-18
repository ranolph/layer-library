#!/bin/sh
set -eu

# Multipart uploads roll over from memory to temporary files before the API
# copies them into the library. Use persistent disk, not the small /tmp tmpfs.
: "${DATA_DIR:=/data}"
export DATA_DIR
mkdir -p "$DATA_DIR/incoming"
TMPDIR="$DATA_DIR/incoming"
export TMPDIR

exec "$@"
