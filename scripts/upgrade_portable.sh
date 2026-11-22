#!/bin/sh
# Apply StarHope-Portable.zip from the application folder without touching data.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
if [ "${STARHOPE_UPGRADE_COPY:-}" != "1" ]; then
    copy=$(mktemp "${TMPDIR:-/tmp}/starhope-upgrade.XXXXXX")
    cp "$0" "$copy"
    STARHOPE_UPGRADE_COPY=1 STARHOPE_UPGRADE_ROOT="$ROOT" exec /bin/sh "$copy"
fi
ROOT=${STARHOPE_UPGRADE_ROOT:-$ROOT}
cd "$ROOT"

ZIP=StarHope-Portable.zip
if [ ! -f "$ZIP" ]; then
    echo "[E_UPGRADE] $ZIP not found in $ROOT." >&2
    exit 1
fi

case "$(uname -s):$(uname -m)" in
    Darwin:arm64) target=macos-arm64 ;;
    Darwin:x86_64) target=macos-x64 ;;
    Linux:x86_64) target=linux-x64 ;;
    *) echo "[E_UPGRADE] Unsupported platform." >&2; exit 1 ;;
esac

PYTHON="runtime/$target/python/bin/python3.14"
if [ ! -x "$PYTHON" ]; then
    echo "[E_UPGRADE] Missing current runtime: $PYTHON" >&2
    exit 1
fi
if [ "${STARHOPE_UPGRADE_NO_STOP:-}" != "1" ]; then
    "$PYTHON" -B scripts/upgrade_shutdown.py "$ROOT" || exit 1
fi
"$PYTHON" - "$ROOT" <<'PY' || exit 1
import sys
from pathlib import Path

lock = Path(sys.argv[1]) / ".system_generated" / "runtime.lock"
if not lock.exists():
    sys.exit(0)
try:
    import fcntl
except ImportError:
    sys.exit(0)
with lock.open("a+b") as handle:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(
            "[E_UPGRADE] The service is still running. "
            "Close it from the web power button first.",
            file=sys.stderr,
        )
        sys.exit(1)
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
PY

if [ -f "$ZIP.sha256" ]; then
    expected=$(awk 'NR==1 {print $1}' "$ZIP.sha256")
    if command -v shasum >/dev/null 2>&1; then
        actual=$(shasum -a 256 "$ZIP" | awk '{print $1}')
    elif command -v sha256sum >/dev/null 2>&1; then
        actual=$(sha256sum "$ZIP" | awk '{print $1}')
    else
        actual=""
    fi
    if [ -n "$actual" ] && [ "$actual" != "$expected" ]; then
        echo "[E_UPGRADE] Checksum mismatch; the archive was not applied." >&2
        exit 1
    fi
fi

stamp=$(date +%Y%m%d-%H%M%S)
backup="data_backup/upgrade-$stamp"
mkdir -p "$backup"
for name in starhope.db starhope.db-wal starhope.db-shm .env; do
    if [ -e "$name" ]; then cp -p "$name" "$backup/"; fi
done
if [ -d static/uploads ]; then cp -R static/uploads "$backup/uploads"; fi
if [ -d .system_generated ]; then cp -R .system_generated "$backup/system_generated"; fi

staging=$(mktemp -d "${TMPDIR:-/tmp}/starhope-staging.XXXXXX")
cleanup() {
    rm -rf "$staging"
    rm -f "$0"
}
trap cleanup EXIT
if command -v unzip >/dev/null 2>&1; then
    unzip -q "$ZIP" -d "$staging"
elif command -v ditto >/dev/null 2>&1; then
    ditto -x -k "$ZIP" "$staging"
else
    echo "[E_UPGRADE] unzip or ditto is required." >&2
    exit 1
fi
if [ ! -f "$staging/main.py" ] || [ ! -d "$staging/runtime" ]; then
    echo "[E_UPGRADE] The archive is not a complete StarHope package." >&2
    exit 1
fi

if [ "$(uname -s)" = "Darwin" ]; then
    /usr/bin/ditto "$staging/" "$ROOT/"
else
    cp -a "$staging/." "$ROOT/"
fi

mv "$ZIP" "$backup/applied.zip"
if [ -f "$ZIP.sha256" ]; then mv "$ZIP.sha256" "$backup/applied.zip.sha256"; fi
echo "[ok] StarHope upgraded; local data was preserved. Backup: $backup"
if [ "${STARHOPE_UPGRADE_NO_START:-}" != "1" ]; then
    exec sh "$ROOT/StarHope.sh"
fi
