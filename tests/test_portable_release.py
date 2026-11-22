"""Portable bundle guards: pinned runtimes, host selection, thin launchers."""
import io
import os
import tarfile
from pathlib import Path

import pytest

from scripts import build_release, portable_launcher

ROOT = Path(__file__).resolve().parents[1]


def test_portable_targets_pin_all_four_desktop_runtimes(monkeypatch):
    monkeypatch.delenv("STARHOPE_PBS_BASE_URL", raising=False)
    assert build_release.PORTABLE_TARGETS == (
        "win-x64", "macos-arm64", "macos-x64", "linux-x64",
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
        ("darwin", "x86_64", "macos-x64"),
        ("linux", "x86_64", "linux-x64"),
    ],
)
def test_portable_launcher_selects_the_matching_runtime(
    monkeypatch, system, machine, expected
):
    monkeypatch.setattr(portable_launcher.sys, "platform", system)
    monkeypatch.setattr(portable_launcher.platform, "machine", lambda: machine)
    assert portable_launcher.runtime_target() == expected


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
        (tmp_path / name).write_bytes(source)
    assert (ROOT / "StarHope.command").read_bytes() == (ROOT / "StarHope.sh").read_bytes()
    assert (ROOT / "StarHope.bat").read_bytes().isascii()

    build_release.stage_portable_launchers(tmp_path)

    batch = (tmp_path / "StarHope.bat").read_bytes()
    assert batch.endswith(b"\r\n")
    assert b"\n" not in batch.replace(b"\r\n", b"")
    if os.name != "nt":
        for name in ("StarHope.command", "StarHope.sh"):
            assert (tmp_path / name).stat().st_mode & 0o111
