#!/bin/bash
# Locate Python; the shared local launcher prepares the environment and opens StarHope.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) || exit 1
cd "$SCRIPT_DIR" || exit 1
if [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" != arm64 ]; then
    echo "macOS 仅支持 Apple Silicon，请使用原生 arm64 终端与 Python；不支持 Intel 或 Rosetta x64。" >&2
    exit 1
fi

python_is_supported() {
    "$1" -c 'import sys, platform; raise SystemExit(0 if sys.version_info[:2] == (3, 14) and (sys.platform != "darwin" or platform.machine().lower() in {"arm64", "aarch64"}) else 1)' \
        >/dev/null 2>&1
}

find_supported_python() {
    for candidate in "$SCRIPT_DIR/venv/bin/python" python3.14 python3; do
        candidate_path=$(command -v "$candidate" 2>/dev/null || true)
        if [ -n "$candidate_path" ] && python_is_supported "$candidate_path"; then
            printf '%s\n' "$candidate_path"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN=$(find_supported_python)
if [ -z "$PYTHON_BIN" ]; then
    echo "未找到 Python 3.14，请安装 Python 3.14 后再启动题库。"
    LAUNCHER_EXIT=1
else
    "$PYTHON_BIN" -B -m scripts.local_launcher
    LAUNCHER_EXIT=$?
fi
if [ "$LAUNCHER_EXIT" -ne 0 ] && [ -t 0 ] && [ "$STARHOPE_NO_PAUSE" != "1" ]; then
    read -r -n 1 -p "按任意键退出..." _unused
    echo ""
fi
exit "$LAUNCHER_EXIT"
