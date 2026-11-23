"""Launch the bundled StarHope runtime for the current operating system.

The portable archive ships one CPython per desktop platform under runtime/.
The thin StarHope.bat / .command / .sh wrappers start the matching interpreter
and hand control to this module, which exposes the shared dependency folder
through PYTHONPATH and then uses the normal local launcher.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def runtime_target():
    """Return the bundled runtime directory name for this host."""
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        if machine in {"arm64", "aarch64"}:
            try:
                major = int(platform.mac_ver()[0].split(".")[0])
            except (ValueError, IndexError):
                raise RuntimeError("无法确认 macOS 版本，当前程序要求 macOS 14 或更新版本。") from None
            if major < 14:
                raise RuntimeError("当前程序要求 Apple Silicon 与 macOS 14 或更新版本。")
            return "macos-arm64"
        raise RuntimeError("macOS 仅支持 Apple Silicon，请使用原生 arm64 Python，不支持 Intel 或 Rosetta x64。")
    if sys.platform.startswith("linux"):
        return "linux-x64"
    if os.name == "nt":
        return "win-x64"
    raise RuntimeError(f"不支持当前平台：{sys.platform}")


def executable_name(target):
    return "python.exe" if target == "win-x64" else "bin/python3.14"


def portable_environment(target, root=None):
    """Validate one bundled runtime and return (environment, interpreter, deps)."""
    base = Path(root or ROOT)
    runtime = base / "runtime" / target
    python = runtime / "python" / executable_name(target)
    site_packages = runtime / "site-packages"
    if not python.is_file():
        raise RuntimeError(f"便携包缺少 {target} 运行时：{python}")
    if not site_packages.is_dir():
        raise RuntimeError(f"便携包缺少 {target} 依赖目录：{site_packages}")
    environment = os.environ.copy()
    environment["STARHOPE_PORTABLE_RUNTIME"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (
            str(site_packages), str(base), environment.get("PYTHONPATH", "")
        ) if value
    )
    return environment, python, site_packages


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    target = runtime_target()
    environment, python, site_packages = portable_environment(target)
    if Path(sys.executable).resolve() != python.resolve():
        raise SystemExit(
            f"请使用便携包根目录的 StarHope 启动脚本，而不是系统 Python：{python}"
        )
    os.environ["STARHOPE_PORTABLE_RUNTIME"] = "1"
    os.environ["PYTHONPATH"] = environment["PYTHONPATH"]
    for entry in (str(site_packages), str(ROOT)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    from scripts.local_launcher import run_launcher

    run_launcher(ROOT)


if __name__ == "__main__":
    main()
