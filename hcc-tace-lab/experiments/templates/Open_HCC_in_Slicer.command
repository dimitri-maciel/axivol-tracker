#!/bin/sh
# Open a new Slicer instance without clearing another scene or user draft.
set -eu
AXIVOL_READER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -d /Applications/Slicer.app ]; then
    printf '%s\n' 'Install 3D Slicer in /Applications/Slicer.app first.' >&2
    exit 1
fi
exec /usr/bin/open -n -a /Applications/Slicer.app --args --no-splash \
    --python-script "$AXIVOL_READER_DIR/open_in_slicer.py"
