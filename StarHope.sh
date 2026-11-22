#!/bin/sh
# Thin portable launcher shared by macOS (.command) and Linux (.sh).
cd "$(dirname "$0")" || exit 1
if [ -f "StarHope-Portable.zip" ]; then
  exec sh ./scripts/upgrade_portable.sh
fi
case "$(uname -s):$(uname -m)" in
  Darwin:arm64) target=macos-arm64 ;;
  Darwin:x86_64) target=macos-x64 ;;
  Linux:x86_64) target=linux-x64 ;;
  *) echo "[E_RUNTIME] Unsupported platform: $(uname -s) $(uname -m)" >&2; exit 1 ;;
esac
python="./runtime/$target/python/bin/python3.14"
if [ ! -x "$python" ]; then
  echo "[E_RUNTIME] Missing $python. Extract the complete StarHope portable package." >&2
  exit 1
fi
exec "$python" -B -m scripts.portable_launcher "$@"
