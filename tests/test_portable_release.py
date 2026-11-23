"""Portable bundle guards: pinned runtimes, host selection, thin launchers."""
import hashlib
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import build_release, portable_launcher, upgrade_shutdown

ROOT = Path(__file__).resolve().parents[1]


def test_portable_targets_pin_three_supported_desktop_runtimes(monkeypatch):
    monkeypatch.delenv("STARHOPE_PBS_BASE_URL", raising=False)
    assert build_release.PORTABLE_TARGETS == (
        "win-x64", "macos-arm64", "linux-x64",
    )
    for target, (triple, digest) in build_release.PORTABLE_RUNTIMES.items():
        assert len(digest) == 64
        int(digest, 16)
        name = build_release.pbs_asset_name(triple)
        assert name == (
            f"cpython-{build_release.PYTHON_VERSION}"
            f"+{build_release.PYTHON_BUILD_STANDALONE_TAG}"
            f"-{triple}-install_only.tar.gz"
        )
        assert build_release.pbs_download_url(triple).startswith(
            "https://github.com/astral-sh/python-build-standalone/"
        )


def test_macos_dependency_install_targets_macos_14_independent_of_host(monkeypatch, tmp_path):
    monkeypatch.setattr(build_release, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(build_release, "uv_executable", lambda: "uv")
    monkeypatch.setenv("MACOSX_DEPLOYMENT_TARGET", "13.0")
    calls = []
    monkeypatch.setattr(build_release.subprocess, "check_call", lambda command, **options: calls.append((command, options)))
    build_release.install_target_dependencies(tmp_path / "packages", "aarch64-apple-darwin")
    assert calls[0][1]["env"]["MACOSX_DEPLOYMENT_TARGET"] == "14.0"
    command = calls[0][0]
    assert command[command.index("--python-platform") + 1] == "aarch64-apple-darwin"
    assert "markitdown==0.1.9" in command


def test_tar_validator_rejects_traversal_before_extraction(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        member = tarfile.TarInfo("../escape.txt")
        member.size = 1
        handle.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(RuntimeError, match="unsafe member"):
        build_release.validate_tar_gz(archive)


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("darwin", "arm64", "macos-arm64"),
        ("linux", "x86_64", "linux-x64"),
    ],
)
def test_portable_launcher_selects_the_matching_runtime(
    monkeypatch, system, machine, expected
):
    monkeypatch.setattr(portable_launcher.sys, "platform", system)
    monkeypatch.setattr(portable_launcher.platform, "machine", lambda: machine)
    monkeypatch.setattr(portable_launcher.platform, "mac_ver", lambda: ("14.0", (), machine))
    assert portable_launcher.runtime_target() == expected


@pytest.mark.parametrize("version", ["13.6", "12.0", ""])
def test_apple_silicon_requires_macos_14_before_dependency_loading(monkeypatch, version):
    monkeypatch.setattr(portable_launcher.sys, "platform", "darwin")
    monkeypatch.setattr(portable_launcher.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(portable_launcher.platform, "mac_ver", lambda: (version, (), "arm64"))
    with pytest.raises(RuntimeError, match="macOS 14"):
        portable_launcher.runtime_target()


def test_intel_macos_is_rejected_by_runtime_and_source_launchers(monkeypatch, tmp_path):
    from scripts import local_launcher
    monkeypatch.setattr(portable_launcher.sys, "platform", "darwin")
    monkeypatch.setattr(portable_launcher.platform, "machine", lambda: "x86_64")
    assert build_release.host_portable_target() == ""
    with pytest.raises(RuntimeError, match="Apple Silicon"):
        portable_launcher.runtime_target()
    with pytest.raises(local_launcher.LauncherError, match="Apple Silicon"):
        local_launcher.prepare_python(tmp_path)
    assert not list(tmp_path.iterdir()), "Unsupported platforms must not modify the environment"


@pytest.mark.skipif(os.name == "nt", reason="POSIX entry points")
@pytest.mark.parametrize("name", ["StarHope.command", "StarHope.sh", "启动题库系统.command", "scripts/upgrade_portable.sh"])
def test_shell_entry_points_reject_intel_macos_before_start_or_upgrade(tmp_path, name):
    root = tmp_path / "app"
    target = root / name
    target.parent.mkdir(parents=True)
    shutil.copy2(ROOT / name, target)
    if "upgrade" in name:
        (root / "StarHope-Portable.zip").touch()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uname = fake_bin / "uname"
    uname.write_text('#!/bin/sh\ncase "$1" in -s) echo Darwin;; -m) echo x86_64;; esac\n')
    uname.chmod(0o755)
    environment = {**os.environ, "PATH": str(fake_bin) + os.pathsep + os.defpath, "STARHOPE_NO_PAUSE": "1"}
    result = subprocess.run(["bash", str(target)], env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "Intel" in result.stderr
    assert not (root / "data_backup").exists()


@pytest.mark.parametrize("name", ["macos-x64", "MACOS-X64"])
def test_retired_intel_runtime_is_rejected_in_staging_and_final_zip(tmp_path, name):
    (tmp_path / "runtime" / name).mkdir(parents=True)
    with pytest.raises(RuntimeError, match="retired Intel"):
        build_release.validate_portable_tree(tmp_path)
    archive = tmp_path / "retired.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(f"runtime/{name}/", b"")
    with pytest.raises(RuntimeError, match="retired Intel"):
        build_release.validate_portable_archive(archive)


def test_portable_environment_shares_one_dependency_folder_and_app_root(tmp_path):
    runtime = tmp_path / "runtime" / "linux-x64"
    (runtime / "python" / "bin").mkdir(parents=True)
    executable = runtime / "python" / "bin" / "python3.14"
    executable.touch()
    site_packages = runtime / "site-packages"
    site_packages.mkdir()

    environment, python, site = portable_launcher.portable_environment(
        "linux-x64", tmp_path
    )

    assert python == executable and site == site_packages
    assert environment["STARHOPE_PORTABLE_RUNTIME"] == "1"
    assert environment["PYTHONPATH"].split(os.pathsep)[:2] == [
        str(site_packages), str(tmp_path),
    ]


def test_launchers_are_thin_and_stage_with_windows_line_endings(tmp_path):
    for name in build_release.PORTABLE_LAUNCHERS:
        source = (ROOT / name).read_bytes()
        assert b"scripts.portable_launcher" in source
        assert b"StarHope-Portable.zip" in source
        (tmp_path / name).write_bytes(source)
    (tmp_path / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts" / "upgrade_portable.sh", tmp_path / "scripts")
    assert (ROOT / "StarHope.command").read_bytes() == (ROOT / "StarHope.sh").read_bytes()
    assert (ROOT / "StarHope.bat").read_bytes().isascii()
    assert (ROOT / "scripts" / "upgrade_portable.ps1").is_file()

    build_release.stage_portable_launchers(tmp_path)

    batch = (tmp_path / "StarHope.bat").read_bytes()
    assert batch.endswith(b"\r\n")
    assert b"\n" not in batch.replace(b"\r\n", b"")
    if os.name != "nt":
        for name in ("StarHope.command", "StarHope.sh", "scripts/upgrade_portable.sh"):
            assert (tmp_path / name).stat().st_mode & 0o111


@pytest.mark.skipif(os.name == "nt", reason="POSIX lock semantics")
def test_upgrade_shutdown_waits_for_the_runtime_lock_to_be_released(tmp_path):
    import fcntl

    state = tmp_path / ".system_generated"
    state.mkdir()
    lock = state / "runtime.lock"
    lock.write_bytes(b"0")
    with lock.open("r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert upgrade_shutdown.lock_is_free(tmp_path) is False
    assert upgrade_shutdown.lock_is_free(tmp_path) is True


def test_upgrade_shutdown_targets_only_this_application_folder(tmp_path):
    expected = hashlib.sha256(
        os.path.normcase(str(tmp_path.resolve())).encode("utf-8")
    ).hexdigest()
    assert upgrade_shutdown.expected_instance(tmp_path) == expected
    assert upgrade_shutdown.candidate_ports(tmp_path) == [8000]
    state = tmp_path / ".system_generated"
    state.mkdir()
    (state / "launcher-settings.json").write_text('{"port": 8123}', encoding="utf-8")
    assert upgrade_shutdown.candidate_ports(tmp_path) == [8123]


def _portable_target():
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        return "macos-arm64" if machine in {"arm64", "aarch64"} else ""
    if system == "Linux" and machine in {"x86_64", "amd64"}:
        return "linux-x64"
    return ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX upgrade helper")
def test_portable_upgrade_overlays_program_and_preserves_local_data(tmp_path):
    target = _portable_target()
    if not target:
        pytest.skip("unsupported host")
    root = tmp_path / "StarHope"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "upgrade_portable.sh", root / "scripts")
    shutil.copy2(ROOT / "scripts" / "upgrade_shutdown.py", root / "scripts")
    shutil.copy2(ROOT / "StarHope.sh", root / "StarHope.sh")
    (root / "StarHope.sh").chmod(0o755)
    wrapper = root / "runtime" / target / "python" / "bin" / "python3.14"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)

    (root / "starhope.db").write_bytes(b"database")
    (root / ".env").write_text("API_KEY=keep\n", encoding="utf-8")
    (root / "static" / "uploads").mkdir(parents=True)
    (root / "static" / "uploads" / "photo.jpg").write_bytes(b"photo")
    (root / ".system_generated").mkdir()
    (root / ".system_generated" / "marker").write_text("state", encoding="utf-8")

    archive = root / "StarHope-Portable.zip"
    with zipfile.ZipFile(archive, "w") as package:
        def add(name, payload, mode=0o644):
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            package.writestr(info, payload)
        add("main.py", "UPGRADED=1\n")
        add(f"runtime/{target}/marker.txt", "runtime\n")
        add("scripts/upgrade_portable.sh", (ROOT / "scripts" / "upgrade_portable.sh").read_bytes(), 0o755)
        add("StarHope.sh", (ROOT / "StarHope.sh").read_bytes(), 0o755)
        add("static/index.html", "new\n")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (root / "StarHope-Portable.zip.sha256").write_text(
        f"{digest}  StarHope-Portable.zip\n", encoding="ascii"
    )

    result = subprocess.run(
        ["/bin/sh", str(root / "scripts" / "upgrade_portable.sh")],
        cwd=root,
        env={
            **os.environ,
            "STARHOPE_UPGRADE_NO_START": "1",
            "STARHOPE_UPGRADE_NO_STOP": "1",
        },
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (root / "main.py").read_text(encoding="utf-8") == "UPGRADED=1\n"
    assert (root / "runtime" / target / "marker.txt").is_file()
    assert (root / "starhope.db").read_bytes() == b"database"
    assert (root / ".env").read_text(encoding="utf-8") == "API_KEY=keep\n"
    assert (root / "static" / "uploads" / "photo.jpg").read_bytes() == b"photo"
    assert (root / ".system_generated" / "marker").read_text(encoding="utf-8") == "state"
    assert not archive.exists()
    backups = list((root / "data_backup").glob("upgrade-*"))
    assert len(backups) == 1
    assert (backups[0] / "applied.zip").is_file()
    assert (backups[0] / "starhope.db").read_bytes() == b"database"
    assert (backups[0] / "uploads" / "photo.jpg").read_bytes() == b"photo"
