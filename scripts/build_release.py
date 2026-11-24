"""Build the verified three-runtime portable StarHope archive.

One staging folder contains Windows, macOS (Apple Silicon) and Linux CPython
runtimes plus a shared application/data root.  The release builder deliberately
fails closed: downloaded runtimes must match their pinned SHA-256 values,
cached downloads are rechecked on every build, and only an explicit application
allowlist is copied into staging directories.  Set STARHOPE_PBS_BASE_URL to a
python-build-standalone mirror base when GitHub is unreachable.
"""

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

import tomllib

from starhope import __version__
from starhope.paths import BUILD_CACHE_DIR, DIST_DIR as PROJECT_DIST_DIR, PROJECT_ROOT


# Paths
BASE_DIR = str(PROJECT_ROOT)
DIST_DIR = str(PROJECT_DIST_DIR)
BUILD_DIR = os.path.join(DIST_DIR, "starhope-windows")
PYTHON_DIR = os.path.join(BUILD_DIR, "python")
WHEELS_DIR = os.path.join(DIST_DIR, "wheels")
SITE_PACKAGES = os.path.join(PYTHON_DIR, "site-packages")
CACHE_DIR = str(BUILD_CACHE_DIR)
MACOS_BUILD_DIR = os.path.join(DIST_DIR, "starhope-macos")
PORTABLE_BUILD_DIR = os.path.join(DIST_DIR, "starhope-portable")
PORTABLE_RUNTIME_DIR = os.path.join(PORTABLE_BUILD_DIR, "runtime")

PYTHON_VERSION = "3.14.10"
PYTHON_ZIP_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)
PYTHON_ZIP_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"
NUGET_ZIP_URL = (
    f"https://api.nuget.org/v3-flatcontainer/python/{PYTHON_VERSION}/"
    f"python.{PYTHON_VERSION}.nupkg"
)
NUGET_ZIP_SHA256 = "f54ba67c9951c3d773ea47d2bb095150c037c399f0423a968bcda22809241784"

# Portable bundle: one shared application/data folder with a self-contained
# CPython for every desktop OS.  Set STARHOPE_PBS_BASE_URL to a mirror prefix
# if GitHub is unreachable; the pinned SHA-256 below is always enforced.
PYTHON_BUILD_STANDALONE_TAG = "20261110"
PYTHON_BUILD_STANDALONE_BASE = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{PYTHON_BUILD_STANDALONE_TAG}"
)
PORTABLE_RUNTIMES = {
    "macos-arm64": (
        "aarch64-apple-darwin",
        "42031ed5f0d38777335252c8c77d64708c8010dd67c128246bdec25000672666",
    ),
    "linux-x64": (
        "x86_64-unknown-linux-gnu",
        "3fe0d7c0538eb5d190c34582992d26230532551355cbd789fabc6591621cfc07",
    ),
}
PORTABLE_WINDOWS_TARGET = "win-x64"
PORTABLE_TARGETS = (PORTABLE_WINDOWS_TARGET, *PORTABLE_RUNTIMES)
PORTABLE_LAUNCHERS = ("StarHope.bat", "StarHope.command", "StarHope.sh")
PORTABLE_REQUIRED_MODULES = (
    "fastapi", "uvicorn", "sqlalchemy", "greenlet", "colorama", "pymupdf",
    "pdf_inspector", "sqlite_vec", "cozo_embedded", "pycozo", "markitdown", "pdfminer", "pdfplumber",
)

# Microsoft Visual C++ Runtime used by the pinned Windows wheels.  The
# production DLLs below come from the Visual Studio 2026 Stable VC\Redist
# payload, not from debug_nonredist and not from an unversioned third-party
# DLL mirror.  Microsoft requires the deployed Runtime to be at least as new
# as the MSVC tools used to build an application; current greenlet/Pillow
# wheels use linker 14.51.
MSVC_REDIST_VSIX_URL = (
    "https://download.visualstudio.microsoft.com/download/pr/"
    "3984a6ef-2ffc-4c07-962e-bf9e9b862819/"
    "24a1c8dba0376d9b008ac2ba430d13c06664fc6f163511fd7866485030e289af/"
    "Microsoft.VC.14.51.CRT.Redist.X64.base.vsix"
)
MSVC_REDIST_VSIX_SHA256 = (
    "24a1c8dba0376d9b008ac2ba430d13c06664fc6f163511fd7866485030e289af"
)
MSVC_REDIST_PACKAGE_ID = "Microsoft.VC.14.51.CRT.Redist.X64.base"
MSVC_REDIST_PACKAGE_VERSION = "14.51.36247"
MSVC_REDIST_MEMBER_ROOT = (
    "Contents/VC/Redist/MSVC/14.51.36231/x64/Microsoft.VC145.CRT"
)
MSVC_RUNTIME_DLLS = {
    "msvcp140.dll": (
        f"{MSVC_REDIST_MEMBER_ROOT}/msvcp140.dll",
        "7c26614e1d733892c2deac7e245ce115504b1d80592dd0a01b08e3e5a55f89ca",
    ),
    "vcruntime140.dll": (
        f"{MSVC_REDIST_MEMBER_ROOT}/vcruntime140.dll",
        "d1f4225df2cd877dbf130d5668a021dce3f94118455ff5ec952061c30afc9ce7",
    ),
    "vcruntime140_1.dll": (
        f"{MSVC_REDIST_MEMBER_ROOT}/vcruntime140_1.dll",
        "a7146c08f89fe5b04541ab507cdb59ff7b44534d4ba3c668a426c6450a03434e",
    ),
}
MSVC_RUNTIME_NOTICE_NAME = "MICROSOFT-VC-RUNTIME-SOURCE.txt"
MSVC_RUNTIME_NOTICE = f"""Microsoft Visual C++ x64 Runtime files

Package: {MSVC_REDIST_PACKAGE_ID} {MSVC_REDIST_PACKAGE_VERSION}
Source: {MSVC_REDIST_VSIX_URL}
Archive SHA-256: {MSVC_REDIST_VSIX_SHA256}
Files: {', '.join(MSVC_RUNTIME_DLLS)}
Supported StarHope target: Windows 10/11 x64

The DLLs are copied unmodified from the production VC\\Redist directory.
Redistribution is subject to the Microsoft Visual Studio license terms:
https://learn.microsoft.com/en-us/visualstudio/releases/2026/redistribution
"""

ROOT_FILE_ALLOWLIST = (
    "main.py",
    ".env.example",
    "覆盖升级说明.txt",
    "pyproject.toml",
    "README.md",
    "LICENSE",
)
STATIC_ALLOWLIST = (
    "index.html",
    "mobile.html",
    "apple-touch-icon.png",
    "favicon.ico",
    "favicon.png",
    "favicon.svg",
    "css",
    "js",
    "lib",
)
MOBILE_REQUIRED_FILES = (
    "static/mobile.html", "static/js/mobile.js", "static/css/mobile.css",
    "static/lib/katex/katex.min.css", "static/lib/katex/katex.min.js",
    "static/lib/katex/contrib/auto-render.min.js", "static/lib/katex/contrib/mhchem.min.js",
    "static/lib/katex/fonts/KaTeX_Main-Regular.woff2",
    "static/lib/marked/marked.min.js", "static/lib/dompurify/purify.min.js",
)
HIDDEN_FILE_ALLOWLIST = {".env.example", ".gitkeep", ".rels"}
# Reviewed local-only maintenance tools and the source-only release builder.
# Match project-relative paths so runtime resources and dependency names survive.
INTERNAL_TOOL_RELEASE_PATHS = frozenset({
    "starhope/association_batch.py",
    "starhope/classification_proposals.py",
    "starhope/difficulty_batch.py",
    "starhope/knowledge_backfill.py",
    "starhope/knowledge_backfill_apply.py",
    "starhope/question_exchange.py",
    "starhope/zhixue_archive_conversion.py",
    "scripts/association_batch.py",
    "scripts/audit_subquestion_numbering.py",
    "scripts/backfill_question_references.py",
    "tests/test_backfill_question_references.py",
    "tests/test_question_references_exchange.py",
    "scripts/classification_proposals.py",
    "scripts/difficulty_batch.py",
    "scripts/difficulty_classify.py",
    "scripts/knowledge_backfill.py",
    "scripts/knowledge_backfill_apply.py",
    "scripts/question_exchange.py",
    "scripts/zhixue_archive_preview.py",
    "scripts/zhixue_formula_check.js",
    "tests/test_zhixue_archive_preview.py",
    "tests/test_zhixue_archive_conversion.py",
    "tests/test_zhixue_render_contract.py",
    "scripts/resources",
    "scripts/migrate_word_math_private_chars.py",
    "scripts/build_release.py",
})
FORBIDDEN_RELEASE_PARTS = {
    ".DS_Store",
    ".git",
    ".github",
    ".pytest_cache",
    ".system_generated",
    "__pycache__",
    "docs",
    "scratch",
    "test_uploads",
    "tests",
    "venv",
}
FORBIDDEN_RELEASE_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".log",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RELEASE_OUTPUT_NAMES = (
    "StarHope-Windows-x64.zip",
    "StarHope-Windows-x64.zip.sha256",
    "StarHope-macOS.zip",
    "StarHope-macOS.zip.sha256",
    "StarHope-Portable.zip",
    "StarHope-Portable.zip.sha256",
)
# These directories are shipped by pinned wheels but are not needed at runtime.
# Keep the list explicit so a newly introduced package payload still fails the
# release allowlist review instead of being silently discarded.
DEPENDENCY_TEST_PATHS = (
    Path("certifi", "tests"),
    Path("colorama", "tests"),
    Path("fastapi", ".agents"),
    Path("greenlet", "tests"),
)
OVERLAY_PROTECTED_PATHS = (
    ".env",
    ".system_generated",
    "data_backup",
    "starhope.db",
    "starhope.db-wal",
    "starhope.db-shm",
    "static/uploads",
    "venv",
)
OVERLAY_MANAGED_ROOTS = (
    "main.py",
    ".env.example",
    "覆盖升级说明.txt",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "starhope",
    "scripts",
    "static",
    "templates",
)
WINDOWS_LAUNCHER_NAME = "启动题库系统.bat"
WINDOWS_LAUNCHER_HELPER = "scripts/windows_launcher.py"


def sha256_file(path):
    """Return the lowercase SHA-256 digest for *path*."""
    digest = hashlib.sha256()
    with open(path, "rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path, expected_sha256, label="download"):
    """Fail closed unless *path* matches a syntactically valid pinned digest."""
    expected = str(expected_sha256).strip().lower()
    if not SHA256_PATTERN.fullmatch(expected):
        raise RuntimeError(f"{label} has no valid pinned SHA-256")
    actual = sha256_file(path)
    if not hmac.compare_digest(actual, expected):
        raise RuntimeError(
            f"{label} SHA-256 mismatch: expected {expected}, got {actual}"
        )
    return actual


def validate_zip(path, label="archive"):
    """Check ZIP structure and every member CRC before extraction."""
    try:
        with zipfile.ZipFile(path, "r") as zip_ref:
            corrupt_member = zip_ref.testzip()
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"{label} is not a valid ZIP archive") from exc
    if corrupt_member is not None:
        raise RuntimeError(f"{label} contains a corrupt member: {corrupt_member}")


def validate_tar_gz(path, label="archive"):
    """Check gzip/tar structure and reject absolute or traversing members."""

    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                member_path = PurePosixPath(member.name.replace("\\", "/"))
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise RuntimeError(
                        f"{label} contains an unsafe member path: {member.name}"
                    )
    except (OSError, tarfile.TarError) as exc:
        raise RuntimeError(f"{label} is not a valid tar.gz archive") from exc


def extract_tar_gz(path, destination):
    """Extract a validated archive with the standard data-safety filter."""

    with tarfile.open(path, "r:gz") as archive:
        archive.extractall(destination, filter="data")


def _validate_https_url(url, label):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RuntimeError(f"{label} download URL must use HTTPS")


def download_verified(url, cache_path, expected_sha256, label, *, validator=validate_zip):
    """Return a verified cache file, downloading atomically when necessary."""
    _validate_https_url(url, label)
    expected = str(expected_sha256).strip().lower()
    if not SHA256_PATTERN.fullmatch(expected):
        raise RuntimeError(f"{label} has no valid pinned SHA-256")

    cache_path = os.fspath(cache_path)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    if os.path.isfile(cache_path):
        try:
            verify_sha256(cache_path, expected, label)
            validator(cache_path, label)
            print(f"[cache] Using verified cached {label}...")
            return cache_path
        except RuntimeError as exc:
            # Keep the old cache untouched until a replacement is fully verified.
            print(f"[warning] Ignoring invalid cached {label}: {exc}")

    print(f"[download] Downloading {label} from {url}...")
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{Path(cache_path).name}.",
        suffix=".download",
        dir=os.path.dirname(cache_path),
    )
    os.close(file_descriptor)
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": f"StarHope-release-builder/{__version__}"},
        )
        with urllib.request.urlopen(request, timeout=60) as response, open(
            temporary_path, "wb"
        ) as target:
            final_url = response.geturl() if hasattr(response, "geturl") else url
            _validate_https_url(final_url, label)
            shutil.copyfileobj(response, target, length=1024 * 1024)

        verify_sha256(temporary_path, expected, label)
        validator(temporary_path, label)
        os.replace(temporary_path, cache_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return cache_path


def _safe_zip_members(zip_ref):
    """Reject absolute paths and parent traversal before ZIP extraction."""
    for member in zip_ref.infolist():
        member_path = Path(member.filename.replace("\\", "/"))
        if member_path.is_absolute() or ".." in member_path.parts:
            raise RuntimeError(f"unsafe ZIP member path: {member.filename}")
        yield member


def _atomic_write_bytes(path, content):
    """Atomically replace *path* with already-validated binary content."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _pe_machine(payload, label="PE file"):
    """Return the COFF machine value from a small, already hash-pinned DLL."""

    payload = bytes(payload)
    if len(payload) < 64 or payload[:2] != b"MZ":
        raise RuntimeError(f"{label} is not a valid PE file")
    pe_offset = int.from_bytes(payload[0x3C:0x40], "little")
    if pe_offset < 0 or pe_offset + 6 > len(payload):
        raise RuntimeError(f"{label} has an invalid PE header")
    if payload[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise RuntimeError(f"{label} has no PE signature")
    return int.from_bytes(payload[pe_offset + 4 : pe_offset + 6], "little")


def validate_release_source():
    """Fail before packaging when release-critical imports or hints are broken."""
    print("[validate] Validating release source imports and type annotations...")
    validation_code = (
        "from typing import get_type_hints; "
        "from starhope.ai_json import parse_ai_json; "
        "get_type_hints(parse_ai_json)"
    )
    subprocess.check_call([sys.executable, "-c", validation_code], cwd=BASE_DIR)

    compile_code = (
        "from pathlib import Path; "
        "from scripts.build_release import _is_internal_tool_release_path; "
        "[compile(p.read_bytes(), str(p), 'exec') "
        "for root in ('starhope', 'scripts') "
        "for p in Path(root).rglob('*.py') "
        "if not _is_internal_tool_release_path(p)]"
    )
    subprocess.check_call([sys.executable, "-c", compile_code], cwd=BASE_DIR)

_PIN_PATTERN = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)"
)


def read_project_dependencies():
    """Return [project].dependencies from the single pyproject manifest."""

    path = Path(BASE_DIR, "pyproject.toml")
    if not path.is_file():
        raise RuntimeError("missing pyproject.toml")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise RuntimeError(f"invalid pyproject.toml: {error}") from error
    dependencies = data.get("project", {}).get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise RuntimeError("pyproject.toml must define [project].dependencies")
    lines = []
    for item in dependencies:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError("pyproject.toml dependencies must be non-empty strings")
        lines.append(item.strip())
    return lines


def _validated_pins(lines, *, allowed_markers=frozenset()):
    pins = set()
    names = set()
    for number, raw in enumerate(lines, 1):
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        if line in pins:
            raise RuntimeError(f"pyproject.toml:{number}: duplicate dependency")
        if line in allowed_markers:
            pins.add(line)
            continue
        match = _PIN_PATTERN.fullmatch(line)
        if not match:
            raise RuntimeError(
                f"pyproject.toml:{number}: require an explicit pin, not host markers or includes"
            )
        package = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if package in names:
            raise RuntimeError(f"pyproject.toml:{number}: duplicate dependency name")
        names.add(package)
        pins.add(line)
    return pins


def resolve_target_requirements(python_version="3.14"):
    """Resolve project dependencies for a target interpreter.

    Target pins are selected here and written without host markers so the
    Windows build never depends on the build machine's marker environment.
    """

    from packaging.requirements import Requirement

    resolved = []
    for line in read_project_dependencies():
        requirement = Requirement(line)
        if requirement.marker is not None and not requirement.marker.evaluate(
            {"python_version": python_version}
        ):
            continue
        resolved.append(line.split(";", 1)[0].strip())
    return resolved


def validate_target_requirements():
    """Validate explicit CPython 3.14 pins without evaluating host markers.

    Every pin must be exact, markers and includes are rejected, and the target
    resolution must match the reviewed runtime lock.
    """
    runtime = _validated_pins(read_project_dependencies())
    windows = _validated_pins(resolve_target_requirements("3.14"))
    if windows != runtime:
        raise RuntimeError("Windows CPython 3.14 pins differ from the reviewed runtime target lock")


def _remove_release_outputs():
    """Remove only known final archives, never unrelated dist contents."""

    for name in RELEASE_OUTPUT_NAMES:
        Path(DIST_DIR, name).unlink(missing_ok=True)


def clean_directories():
    print("[build] Cleaning old directories...")
    os.makedirs(DIST_DIR, exist_ok=True)
    for path in (BUILD_DIR, WHEELS_DIR, MACOS_BUILD_DIR, PORTABLE_BUILD_DIR):
        shutil.rmtree(path, ignore_errors=True)
    _remove_release_outputs()
    os.makedirs(PYTHON_DIR, exist_ok=True)
    os.makedirs(WHEELS_DIR, exist_ok=True)
    os.makedirs(SITE_PACKAGES, exist_ok=True)


def download_python(python_dir=None):
    destination = Path(python_dir or PYTHON_DIR)
    cache_path = os.path.join(CACHE_DIR, "python_embed.zip")
    download_verified(
        PYTHON_ZIP_URL,
        cache_path,
        PYTHON_ZIP_SHA256,
        f"Python {PYTHON_VERSION} embedded runtime",
    )
    print("[build] Extracting Python...")
    os.makedirs(destination, exist_ok=True)
    with zipfile.ZipFile(cache_path, "r") as zip_ref:
        zip_ref.extractall(destination, members=_safe_zip_members(zip_ref))


def download_and_extract_sqlite(python_dir=None, site_packages=None):
    destination = Path(python_dir or PYTHON_DIR)
    packages = Path(site_packages or SITE_PACKAGES)
    cache_path = os.path.join(CACHE_DIR, "python_nuget.zip")
    download_verified(
        NUGET_ZIP_URL,
        cache_path,
        NUGET_ZIP_SHA256,
        "official CPython NuGet package",
    )

    print("[build] Extracting sqlite3 binaries...")
    required_members = {
        "tools/DLLs/_sqlite3.pyd": os.path.join(destination, "_sqlite3.pyd"),
        "tools/DLLs/sqlite3.dll": os.path.join(destination, "sqlite3.dll"),
    }
    extracted_members = set()
    with zipfile.ZipFile(cache_path, "r") as zip_ref:
        _ = list(_safe_zip_members(zip_ref))
        for member in zip_ref.namelist():
            if member in required_members:
                target_path = required_members[member]
                with zip_ref.open(member) as source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)
                extracted_members.add(member)
            elif member.startswith("tools/Lib/sqlite3/") and not member.endswith("/"):
                relative_path = Path(member).relative_to("tools/Lib")
                target_path = Path(packages, relative_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zip_ref.open(member) as source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)

    missing = sorted(set(required_members) - extracted_members)
    if missing:
        raise RuntimeError(f"official CPython NuGet package is missing: {missing}")
    print("[ok] sqlite3 binaries injected successfully.")


def download_and_extract_msvc_runtime(python_dir=None):
    """Inject the pinned production x64 MSVC Runtime from Microsoft's VSIX."""

    destination = Path(python_dir or PYTHON_DIR)
    cache_path = os.path.join(CACHE_DIR, "msvc145_runtime_x64.vsix")
    cache_path = download_verified(
        MSVC_REDIST_VSIX_URL,
        cache_path,
        MSVC_REDIST_VSIX_SHA256,
        f"{MSVC_REDIST_PACKAGE_ID} {MSVC_REDIST_PACKAGE_VERSION}",
    )

    print("[build] Extracting verified Microsoft Visual C++ x64 Runtime DLLs...")
    with zipfile.ZipFile(cache_path, "r") as zip_ref:
        safe_members = list(_safe_zip_members(zip_ref))
        name_set = {member.filename for member in safe_members if not member.is_dir()}

        validated_payloads = {}
        for filename, (member_name, expected_hash) in MSVC_RUNTIME_DLLS.items():
            if "debug_nonredist" in member_name.casefold():
                raise RuntimeError("debug_nonredist files cannot enter a Release")
            if member_name not in name_set:
                raise RuntimeError(
                    f"Microsoft VC Runtime VSIX is missing production member: {member_name}"
                )
            payload = zip_ref.read(member_name)
            actual_hash = hashlib.sha256(payload).hexdigest()
            if not hmac.compare_digest(actual_hash, expected_hash):
                raise RuntimeError(
                    f"Microsoft VC Runtime member SHA-256 mismatch: {member_name}"
                )
            if _pe_machine(payload, member_name) != 0x8664:
                raise RuntimeError(
                    f"Microsoft VC Runtime member is not x64: {member_name}"
                )
            validated_payloads[filename] = payload

    for filename, payload in validated_payloads.items():
        _atomic_write_bytes(destination / filename, payload)

    _atomic_write_text(
        destination / MSVC_RUNTIME_NOTICE_NAME,
        MSVC_RUNTIME_NOTICE,
    )
    print("[ok] Microsoft VC Runtime injected: " + ", ".join(validated_payloads))


def configure_python_path():
    print("[build] Configuring python314._pth...")
    pth_file = os.path.join(PYTHON_DIR, "python314._pth")
    if not os.path.isfile(pth_file):
        raise RuntimeError("embedded Python archive is missing python314._pth")
    with open(pth_file, "r", encoding="utf-8") as file_handle:
        lines = file_handle.read().splitlines()

    new_lines = []
    application_root_added = False
    site_packages_added = False
    for line in lines:
        if line.strip() == "#import site":
            new_lines.append("import site")
        else:
            new_lines.append(line)
        if line.strip() == ".":
            # Entries in python314._pth are relative to python/python.exe, not
            # to the launcher's working directory.  The application modules
            # (main.py, starhope/, scripts/) live one directory above it.
            new_lines.append("..")
            application_root_added = True
            new_lines.append("site-packages")
            site_packages_added = True
    if not application_root_added or not site_packages_added:
        raise RuntimeError("embedded Python path file has no application path entry")
    with open(pth_file, "w", encoding="utf-8", newline="\n") as file_handle:
        file_handle.write("\n".join(new_lines) + "\n")


def uv_executable():
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "uv is required for Release builds; install it from https://docs.astral.sh/uv/"
        )
    return uv


def install_target_dependencies(site_packages, python_platform):
    """Install the locked dependencies for one target platform with uv."""

    print(f"[build] Installing {python_platform} target dependencies with uv...")
    validate_target_requirements()
    os.makedirs(site_packages, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    cmd = [
        uv_executable(),
        "pip",
        "install",
        "--python-platform",
        python_platform,
        "--python-version",
        "3.14",
        "--target",
        os.fspath(site_packages),
        "--cache-dir",
        os.path.join(CACHE_DIR, "uv"),
        "--no-config",
        "--no-progress",
        *resolve_target_requirements("3.14"),
    ]
    print(f"Running command: {subprocess.list2cmdline(cmd)}")
    environment = os.environ.copy()
    if python_platform == "aarch64-apple-darwin":
        environment["MACOSX_DEPLOYMENT_TARGET"] = "14.0"
    subprocess.check_call(cmd, cwd=BASE_DIR, env=environment)
    _prune_dependency_test_artifacts(site_packages)


def download_and_extract_wheels(site_packages=None, python_platform="x86_64-pc-windows-msvc"):
    install_target_dependencies(Path(site_packages or SITE_PACKAGES), python_platform)


def _prune_dependency_test_artifacts(site_packages):
    """Remove reviewed, non-runtime test payloads shipped by pinned wheels."""

    root = Path(site_packages)
    for relative_path in DEPENDENCY_TEST_PATHS:
        shutil.rmtree(root / relative_path, ignore_errors=True)


def pbs_asset_name(triple):
    return (
        f"cpython-{PYTHON_VERSION}+{PYTHON_BUILD_STANDALONE_TAG}-"
        f"{triple}-install_only.tar.gz"
    )


def pbs_download_url(triple):
    """Return a pinned python-build-standalone URL, with an optional mirror base."""

    base = os.environ.get(
        "STARHOPE_PBS_BASE_URL", PYTHON_BUILD_STANDALONE_BASE
    ).strip().rstrip("/")
    _validate_https_url(base, "python-build-standalone")
    return f"{base}/{urllib.parse.quote(pbs_asset_name(triple))}"


def portable_target_dir(target):
    return Path(PORTABLE_RUNTIME_DIR, target)


def configure_portable_python_path(python_dir):
    """Point the embedded Windows interpreter at the shared app and dependencies."""

    python_dir = Path(python_dir)
    path_file = python_dir / "python314._pth"
    if not path_file.is_file():
        raise RuntimeError("embedded Python archive is missing python314._pth")
    new_lines = []
    application_root_added = False
    site_packages_added = False
    for line in path_file.read_text(encoding="utf-8").splitlines():
        if line.strip() == "#import site":
            new_lines.append("import site")
        else:
            new_lines.append(line)
        if line.strip() == ".":
            # python/ -> win-x64/ -> runtime/ -> application root
            new_lines.append("../../..")
            application_root_added = True
            new_lines.append("../site-packages")
            site_packages_added = True
    if not application_root_added or not site_packages_added:
        raise RuntimeError("embedded Python path file has no application path entry")
    path_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8", newline="\n")


def build_windows_portable_runtime(target_root):
    target_root = Path(target_root)
    python_dir = target_root / "python"
    site_packages = target_root / "site-packages"
    print("[build] Building Windows x64 portable runtime...")
    shutil.rmtree(target_root, ignore_errors=True)
    os.makedirs(python_dir, exist_ok=True)
    os.makedirs(site_packages, exist_ok=True)
    download_python(python_dir)
    download_and_extract_sqlite(python_dir, site_packages)
    download_and_extract_msvc_runtime(python_dir)
    configure_portable_python_path(python_dir)
    install_target_dependencies(site_packages, "x86_64-pc-windows-msvc")
    validate_windows_portable_runtime(target_root)


def validate_windows_portable_runtime(target_root):
    target_root = Path(target_root)
    required = (
        target_root / "python" / "python.exe",
        target_root / "python" / "python314._pth",
        target_root / "python" / "_sqlite3.pyd",
        target_root / "python" / "sqlite3.dll",
        *(target_root / "python" / name for name in MSVC_RUNTIME_DLLS),
        target_root / "python" / MSVC_RUNTIME_NOTICE_NAME,
        *(target_root / "site-packages" / name for name in PORTABLE_REQUIRED_MODULES),
        target_root / "site-packages" / "sqlite3",
    )
    missing = [str(path.relative_to(target_root)) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Windows portable runtime is missing: {missing}")
    path_lines = {
        line.strip()
        for line in (target_root / "python" / "python314._pth")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required_lines = {".", "../../..", "../site-packages", "import site"}
    missing_lines = sorted(required_lines - path_lines)
    if missing_lines:
        raise RuntimeError(
            f"Windows portable python314._pth is missing: {missing_lines}"
        )


def build_standalone_runtime(target, target_root):
    triple, expected_sha256 = PORTABLE_RUNTIMES[target]
    target_root = Path(target_root)
    print(f"[build] Downloading Python {PYTHON_VERSION} {target} runtime...")
    archive = download_verified(
        pbs_download_url(triple),
        os.path.join(CACHE_DIR, pbs_asset_name(triple)),
        expected_sha256,
        f"Python {PYTHON_VERSION} {target} runtime",
        validator=validate_tar_gz,
    )
    print(f"[build] Extracting {target} Python runtime...")
    shutil.rmtree(target_root, ignore_errors=True)
    os.makedirs(target_root)
    extract_tar_gz(archive, target_root)
    python_dir = target_root / "python"
    executable = python_dir / "bin" / "python3.14"
    if not executable.is_file():
        raise RuntimeError(f"{target} archive has no python/bin/python3.14")
    for candidate in (python_dir / "bin").glob("python*"):
        if candidate.is_file():
            candidate.chmod(candidate.stat().st_mode | 0o111)
    install_target_dependencies(target_root / "site-packages", triple)
    validate_standalone_runtime(target, target_root)


def host_portable_target():
    if sys.platform.startswith("linux"):
        import platform as host_platform
        return "linux-x64" if host_platform.machine().lower() in {"x86_64", "amd64"} else ""
    if sys.platform == "darwin":
        import platform as host_platform
        machine = host_platform.machine().lower()
        if machine in {"arm64", "aarch64"}:
            return "macos-arm64"
        return ""
    if os.name == "nt":
        return "win-x64"
    return ""


def validate_standalone_runtime(target, target_root):
    target_root = Path(target_root)
    site_packages = target_root / "site-packages"
    missing = [name for name in PORTABLE_REQUIRED_MODULES if not (site_packages / name).exists()]
    executable = target_root / "python" / "bin" / "python3.14"
    if not executable.is_file():
        missing.append("python/bin/python3.14")
    if missing:
        raise RuntimeError(f"{target} portable runtime is missing: {missing}")
    if host_portable_target() != target:
        return
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (
            str(site_packages), str(PORTABLE_BUILD_DIR), environment.get("PYTHONPATH", "")
        ) if value
    )
    smoke = (
        "import fastapi,uvicorn,sqlalchemy,greenlet,colorama,pymupdf;"
        "import pdf_inspector,sqlite_vec,cozo_embedded,pycozo;"
        "from markitdown.converters import PdfConverter;import pdfminer,pdfplumber;"
        "sqlite_vec.loadable_path();import starhope.paths,scripts.portable_launcher"
    )
    subprocess.check_call(
        [str(executable), "-B", "-c", smoke],
        cwd=PORTABLE_BUILD_DIR,
        env=environment,
    )


def validate_portable_tree(root):
    root = Path(root)
    runtime_root = root / "runtime"
    if runtime_root.is_dir() and any(p.name.casefold() == "macos-x64" for p in runtime_root.iterdir()):
        raise RuntimeError("portable staging tree contains the retired Intel macOS runtime")
    required = {
        "main.py",
        "pyproject.toml",
        "覆盖升级说明.txt",
        "README.md",
        "starhope/__init__.py",
        "scripts/portable_launcher.py",
        "scripts/local_launcher.py",
        "scripts/upgrade_portable.sh",
        "scripts/upgrade_portable.ps1",
        "scripts/upgrade_shutdown.py",
        "static/index.html",
        *MOBILE_REQUIRED_FILES,
        "static/uploads/.gitkeep",
        *PORTABLE_LAUNCHERS,
        "runtime/win-x64/python/python.exe",
        "runtime/win-x64/python/python314._pth",
        *(f"runtime/{target}/python/bin/python3.14" for target in PORTABLE_RUNTIMES),
        *(f"runtime/{target}/site-packages/fastapi" for target in PORTABLE_TARGETS),
    }
    missing = sorted(name for name in required if not (root / name).exists())
    if missing:
        raise RuntimeError(f"portable staging tree is missing required files: {missing}")
    forbidden = (
        "starhope.db", "starhope.db-wal", "starhope.db-shm", ".env",
        ".system_generated", "data_backup", "venv",
    )
    present = [name for name in forbidden if (root / name).exists()]
    if present:
        raise RuntimeError(f"portable staging tree contains local data: {present}")


def validate_portable_archive(path):
    validate_zip(path, Path(path).name)
    with zipfile.ZipFile(path, "r") as archive:
        names = set(archive.namelist())
        if any(name.replace("\\", "/").casefold().startswith("runtime/macos-x64/") for name in names):
            raise RuntimeError("finished portable archive contains the retired Intel macOS runtime")
        required = {
            *MOBILE_REQUIRED_FILES,
            "StarHope.bat", "StarHope.command", "StarHope.sh",
            "scripts/portable_launcher.py",
            "scripts/upgrade_portable.sh",
            "scripts/upgrade_portable.ps1",
            "scripts/upgrade_shutdown.py",
            "runtime/win-x64/python/python.exe",
            *(f"runtime/{target}/python/bin/python3.14" for target in PORTABLE_RUNTIMES),
        }
        missing = sorted(required - names)
        if missing:
            raise RuntimeError(f"finished portable archive is missing: {missing}")
        for name in (
            "StarHope.command", "StarHope.sh",
            *(f"runtime/{target}/python/bin/python3.14" for target in PORTABLE_RUNTIMES),
        ):
            info = archive.getinfo(name)
            if info.create_system == 3 and not (info.external_attr >> 16) & 0o111:
                raise RuntimeError(f"finished portable archive member is not executable: {name}")


def zip_portable_release():
    print("[build] Zipping portable release package...")
    validate_portable_tree(PORTABLE_BUILD_DIR)
    archive_path = Path(DIST_DIR, "StarHope-Portable.zip")
    checksum_path = Path(str(archive_path) + ".sha256")
    archive_path.unlink(missing_ok=True)
    checksum_path.unlink(missing_ok=True)
    try:
        archive_path = Path(
            shutil.make_archive(
                os.path.join(DIST_DIR, "StarHope-Portable"), "zip", PORTABLE_BUILD_DIR
            )
        )
        validate_portable_archive(archive_path)
        checksum_path = _write_archive_checksum(archive_path)
    except Exception:
        archive_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        raise
    print(f"[ok] Portable archive created: {archive_path}")
    print(f"[ok] Archive checksum created: {checksum_path}")
    return os.fspath(archive_path)


def build_portable_release():
    """Build one folder holding every desktop runtime and a shared data root."""

    if os.name == "nt":
        raise RuntimeError(
            "build the portable bundle on macOS or Linux so ZIP executable bits are preserved"
        )
    print("[build] Building the Windows, macOS and Linux portable bundle...")
    shutil.rmtree(PORTABLE_BUILD_DIR, ignore_errors=True)
    copy_app_files(PORTABLE_BUILD_DIR, PORTABLE_LAUNCHERS)
    stage_portable_launchers(PORTABLE_BUILD_DIR)
    build_windows_portable_runtime(portable_target_dir(PORTABLE_WINDOWS_TARGET))
    for target in PORTABLE_RUNTIMES:
        build_standalone_runtime(target, portable_target_dir(target))
    validate_portable_tree(PORTABLE_BUILD_DIR)
    return zip_portable_release()


def _is_internal_tool_release_path(relative_path):
    name = PurePosixPath(Path(relative_path).as_posix()).as_posix().casefold()
    return any(
        name == excluded or name.startswith(excluded + "/")
        for excluded in INTERNAL_TOOL_RELEASE_PATHS
    )


def _release_ignore(directory, names):
    try:
        relative_directory = Path(directory).relative_to(Path(BASE_DIR))
    except ValueError:
        relative_directory = None
    ignored = set()
    for name in names:
        if (
            name.startswith(".")
            or name in FORBIDDEN_RELEASE_PARTS
            or name.endswith((".pyc", ".pyo"))
            or (
                relative_directory is not None
                and _is_internal_tool_release_path(relative_directory / name)
            )
        ):
            ignored.add(name)
    return ignored


def _release_path_violation(relative_path):
    """Return why a package member crosses a reviewed release boundary."""

    path = PurePosixPath(Path(relative_path).as_posix())
    name = path.as_posix()
    if _is_internal_tool_release_path(path):
        return "non-runtime source tooling"
    if name == "static/uploads/.gitkeep":
        return None
    if name == ".env" or name.startswith(".env/"):
        return "local API configuration"
    if name == ".system_generated" or name.startswith(".system_generated/"):
        return "local runtime state"
    if name == "data_backup" or name.startswith("data_backup/"):
        return "local backups and metadata"
    if name == "venv" or name.startswith("venv/"):
        return "local virtual environment"
    if name == "static/uploads" or name.startswith("static/uploads/"):
        return "user uploads"
    if path.name.lower().endswith(tuple(FORBIDDEN_RELEASE_SUFFIXES)):
        return "persistent database or runtime artifact"
    return None


def _overlay_managed_roots(platform_name):
    roots = list(OVERLAY_MANAGED_ROOTS)
    if platform_name == "windows-x64":
        roots.extend(("启动题库系统.bat", "python"))
    elif platform_name == "macos":
        roots.append("启动题库系统.command")
    else:
        raise RuntimeError(f"unknown release platform: {platform_name}")
    return roots


def _copy_allowlisted_entry(source, destination):
    if source.is_dir():
        shutil.copytree(source, destination, ignore=_release_ignore)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    else:
        raise RuntimeError(f"required release source is missing: {source}")


def copy_app_files(destination, launcher_name=None):
    """Copy only approved application assets into a release staging tree."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    print(f"[build] Copying allowlisted application files to {destination}...")

    for relative_name in ROOT_FILE_ALLOWLIST:
        _copy_allowlisted_entry(
            Path(BASE_DIR, relative_name), destination / relative_name
        )
    launcher_names = ()
    if isinstance(launcher_name, str):
        launcher_names = (launcher_name,)
    elif launcher_name:
        launcher_names = tuple(launcher_name)
    for name in launcher_names:
        _copy_allowlisted_entry(Path(BASE_DIR, name), destination / name)
    _copy_allowlisted_entry(Path(BASE_DIR, "starhope"), destination / "starhope")
    _copy_allowlisted_entry(Path(BASE_DIR, "scripts"), destination / "scripts")

    static_destination = destination / "static"
    static_destination.mkdir(exist_ok=True)
    for relative_name in STATIC_ALLOWLIST:
        _copy_allowlisted_entry(
            Path(BASE_DIR, "static", relative_name),
            static_destination / relative_name,
        )
    uploads_directory = static_destination / "uploads"
    uploads_directory.mkdir(parents=True, exist_ok=True)
    (uploads_directory / ".gitkeep").touch()

    templates_source = Path(BASE_DIR, "templates")
    if templates_source.is_dir():
        _copy_allowlisted_entry(templates_source, destination / "templates")


def stage_portable_launchers(destination):
    """Normalize the batch launcher and keep the Unix launchers executable."""

    destination = Path(destination)
    batch = destination / "StarHope.bat"
    try:
        text = batch.read_text(encoding="ascii")
    except UnicodeDecodeError as exc:
        raise RuntimeError("StarHope.bat must contain ASCII bytes only") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    batch.write_bytes(normalized.replace("\n", "\r\n").encode("ascii"))
    validate_windows_launcher(batch)
    for name in ("StarHope.command", "StarHope.sh", "scripts/upgrade_portable.sh"):
        path = destination / name
        path.chmod(0o755)


def create_launcher():
    """Write the reviewed Windows launcher with cmd.exe-safe CRLF endings."""
    launcher_source = Path(BASE_DIR, WINDOWS_LAUNCHER_NAME)
    launcher_destination = Path(BUILD_DIR, launcher_source.name)
    if not launcher_source.is_file():
        raise RuntimeError(f"missing Windows launcher: {launcher_source}")
    launcher_payload = launcher_source.read_bytes()
    if launcher_payload.startswith(b"\xef\xbb\xbf"):
        raise RuntimeError("Windows launcher must not contain a UTF-8 BOM")
    try:
        launcher_text = launcher_payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RuntimeError("Windows launcher must contain ASCII bytes only") from exc
    launcher_destination.parent.mkdir(parents=True, exist_ok=True)
    normalized_text = launcher_text.replace("\r\n", "\n").replace("\r", "\n")
    launcher_destination.write_bytes(
        normalized_text.replace("\n", "\r\n").encode("utf-8")
    )
    validate_windows_launcher(launcher_destination)


def _validate_windows_launcher_bytes(payload, label):
    """Reject batch launchers that cmd.exe cannot parse consistently."""
    if not payload:
        raise RuntimeError(f"{label} is empty")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise RuntimeError(f"{label} must not contain a UTF-8 BOM")
    if not payload.isascii():
        raise RuntimeError(f"{label} must contain ASCII bytes only")
    remaining = payload.replace(b"\r\n", b"")
    if b"\r" in remaining or b"\n" in remaining or not payload.endswith(b"\r\n"):
        raise RuntimeError(f"{label} must use CRLF line endings only")


def validate_windows_launcher(path):
    """Validate the raw bytes of a staged Windows batch launcher."""
    launcher_path = Path(path)
    if not launcher_path.is_file():
        raise RuntimeError(f"Windows launcher is missing: {launcher_path}")
    _validate_windows_launcher_bytes(
        launcher_path.read_bytes(), f"Windows launcher {launcher_path.name}"
    )


def _release_files(root):
    return sorted(path for path in Path(root).rglob("*") if path.is_file())


def assert_release_tree_clean(root, platform_name):
    """Enforce release allowlist invariants before an archive can be created."""
    root = Path(root)
    required = {
        "main.py",
        "pyproject.toml",
        "覆盖升级说明.txt",
        "starhope/__init__.py",
        "scripts/release_overlay.py",
        "scripts/local_launcher.py",
        "static/index.html",
        *MOBILE_REQUIRED_FILES,
        "static/uploads/.gitkeep",
    }
    if platform_name == "windows-x64":
        required.update(
            {
                "python/python.exe",
                "python/_sqlite3.pyd",
                *(f"python/{name}" for name in MSVC_RUNTIME_DLLS),
                f"python/{MSVC_RUNTIME_NOTICE_NAME}",
                "启动题库系统.bat",
                WINDOWS_LAUNCHER_HELPER,
            }
        )
    elif platform_name == "macos":
        required.add("启动题库系统.command")
    else:
        raise RuntimeError(f"unknown release platform: {platform_name}")

    relative_files = {path.relative_to(root).as_posix() for path in _release_files(root)}
    missing = sorted(required - relative_files)
    if missing:
        raise RuntimeError(f"release staging tree is missing required files: {missing}")

    violations = []
    for directory in root.rglob("*"):
        if directory.is_dir() and _is_internal_tool_release_path(directory.relative_to(root)):
            violations.append(directory.relative_to(root).as_posix())
    for file_path in _release_files(root):
        relative_path = file_path.relative_to(root)
        persistence_violation = _release_path_violation(relative_path)
        if persistence_violation:
            violations.append(
                f"{relative_path.as_posix()} ({persistence_violation})"
            )
            continue
        for part in relative_path.parts:
            if part in FORBIDDEN_RELEASE_PARTS:
                violations.append(relative_path.as_posix())
                break
            if part.startswith(".") and part not in HIDDEN_FILE_ALLOWLIST:
                violations.append(relative_path.as_posix())
                break
        else:
            lower_name = file_path.name.lower()
            if any(lower_name.endswith(suffix) for suffix in FORBIDDEN_RELEASE_SUFFIXES):
                violations.append(relative_path.as_posix())
    if violations:
        raise RuntimeError(
            "release staging tree contains forbidden artifacts: "
            + ", ".join(sorted(set(violations))[:20])
        )


def write_release_manifest(root, platform_name):
    """Write a deterministic per-file SHA-256 manifest inside the release."""
    root = Path(root)
    manifest_path = root / "RELEASE-MANIFEST.json"
    entries = []
    for file_path in _release_files(root):
        if file_path == manifest_path:
            continue
        entries.append(
            {
                "path": file_path.relative_to(root).as_posix(),
                "sha256": sha256_file(file_path),
                "size": file_path.stat().st_size,
            }
        )
    manifest = {
        "app_version": __version__,
        "files": entries,
        "overlay": {
            "managed_roots": _overlay_managed_roots(platform_name),
            "protected_paths": list(OVERLAY_PROTECTED_PATHS),
            "schema_version": 1,
        },
        "platform": platform_name,
        "schema_version": 1,
    }
    _atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest_path


def _atomic_write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def validate_staged_python_sources(root):
    """Compile staged Python sources without importing data-bearing modules."""
    smoke_code = (
        "from pathlib import Path; "
        "files=list(Path('.').joinpath('starhope').rglob('*.py'))"
        "+list(Path('.').joinpath('scripts').rglob('*.py'))+[Path('main.py')]; "
        "[compile(p.read_bytes(), str(p), 'exec') for p in files]"
    )
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.check_call(
        [sys.executable, "-c", smoke_code], cwd=root, env=environment
    )


def validate_windows_runtime(root):
    """Run the embedded runtime on Windows, otherwise verify its critical layout."""
    root = Path(root)
    python_executable = root / "python" / "python.exe"
    required = (
        python_executable,
        root / "python" / "python314._pth",
        root / "python" / "_sqlite3.pyd",
        root / "python" / "sqlite3.dll",
        root / "python" / "site-packages" / "fastapi",
        root / "python" / "site-packages" / "uvicorn",
        root / "python" / "site-packages" / "sqlalchemy",
        root / "python" / "site-packages" / "greenlet",
        root / "python" / "site-packages" / "colorama",
        root / "python" / "site-packages" / "pymupdf",
        root / "python" / "site-packages" / "pdf_inspector",
        root / "python" / "site-packages" / "markitdown",
        root / "python" / "site-packages" / "pdfminer",
        root / "python" / "site-packages" / "pdfplumber",
        root / "python" / "site-packages" / "sqlite_vec",
        root / "python" / "site-packages" / "cozo_embedded",
        root / "python" / "site-packages" / "pycozo",
        root / "python" / MSVC_RUNTIME_NOTICE_NAME,
        *(root / "python" / name for name in MSVC_RUNTIME_DLLS),
    )
    missing = [str(path.relative_to(root)) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Windows runtime smoke check is missing: {missing}")
    path_lines = {
        line.strip()
        for line in (root / "python" / "python314._pth").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required_path_lines = {".", "..", "site-packages", "import site"}
    missing_path_lines = sorted(required_path_lines - path_lines)
    if missing_path_lines:
        raise RuntimeError(
            "Windows embedded runtime cannot import the application root; "
            f"python314._pth is missing: {missing_path_lines}"
        )
    if os.name == "nt":
        smoke_code = (
            "import ctypes, pathlib, sys; "
            "runtime=pathlib.Path(sys.executable).with_name('msvcp140.dll').resolve(); "
            "ctypes.WinDLL(str(runtime)); "
            "import sqlite3, fastapi, uvicorn, sqlalchemy, greenlet, colorama; "
            "import pymupdf, pdf_inspector, sqlite_vec, cozo_embedded, pycozo; "
            "from markitdown.converters import PdfConverter; import pdfminer,pdfplumber; "
            "import starhope.ai_json, scripts; "
            "assert sqlite_vec.loadable_path().endswith('vec0')"
        )
        subprocess.check_call(
            [
                str(python_executable),
                "-B",  # Smoke imports must not dirty the release staging tree.
                "-c",
                smoke_code,
            ],
            cwd=root,
        )


def verify_zip_archive(zip_path, required_members):
    """Verify a finished archive against its embedded per-file manifest."""
    validate_zip(zip_path, Path(zip_path).name)
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        file_infos = {}
        collision_keys = set()
        for info in zip_ref.infolist():
            raw_name = info.filename
            name = raw_name[:-1] if raw_name.endswith("/") else raw_name
            if not name:
                continue
            member_path = PurePosixPath(name)
            if (
                "\\" in name
                or member_path.is_absolute()
                or member_path.as_posix() != name
                or any(part in {"", ".", ".."} or ":" in part for part in member_path.parts)
            ):
                raise RuntimeError(
                    f"finished release archive contains an unsafe path: {raw_name}"
                )
            if _is_internal_tool_release_path(member_path):
                raise RuntimeError(
                    "finished release archive contains forbidden artifacts: " + name
                )
            if info.is_dir():
                continue
            collision_key = name.casefold()
            if collision_key in collision_keys:
                raise RuntimeError(
                    f"finished release archive contains a duplicate path: {name}"
                )
            collision_keys.add(collision_key)
            file_infos[name] = info

        names = set(file_infos)
        missing = sorted(set(required_members) - names)
        if missing:
            raise RuntimeError(f"finished release archive is missing: {missing}")

        violations = []
        for name in names:
            parts = PurePosixPath(name).parts
            persistence_violation = _release_path_violation(name)
            if persistence_violation:
                violations.append(f"{name} ({persistence_violation})")
                continue
            if any(part in FORBIDDEN_RELEASE_PARTS for part in parts):
                violations.append(name)
            if any(
                part.startswith(".") and part not in HIDDEN_FILE_ALLOWLIST
                for part in parts
            ):
                violations.append(name)
            if any(name.lower().endswith(suffix) for suffix in FORBIDDEN_RELEASE_SUFFIXES):
                violations.append(name)
        if violations:
            raise RuntimeError(
                "finished release archive contains forbidden artifacts: "
                + ", ".join(sorted(set(violations))[:20])
            )

        manifest_info = file_infos.get("RELEASE-MANIFEST.json")
        if manifest_info is None:
            raise RuntimeError("finished release archive has no RELEASE-MANIFEST.json")
        if manifest_info.file_size > 8 * 1024 * 1024:
            raise RuntimeError("finished release manifest is unexpectedly large")
        try:
            with zip_ref.open(manifest_info, "r") as manifest_stream:
                manifest_bytes = manifest_stream.read(8 * 1024 * 1024 + 1)
            if len(manifest_bytes) > 8 * 1024 * 1024:
                raise RuntimeError("finished release manifest is unexpectedly large")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("finished release manifest is invalid JSON") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise RuntimeError("finished release manifest schema is unsupported")
        if manifest.get("app_version") != __version__:
            raise RuntimeError("finished release manifest version does not match the app")
        if manifest.get("platform") not in {"macos", "windows-x64"}:
            raise RuntimeError("finished release manifest platform is invalid")
        missing_mobile = sorted(set(MOBILE_REQUIRED_FILES) - file_infos.keys())
        if missing_mobile:
            raise RuntimeError(f"finished release is missing mobile assets: {missing_mobile}")
        overlay = manifest.get("overlay")
        expected_managed_roots = _overlay_managed_roots(manifest.get("platform"))
        if overlay != {
            "managed_roots": expected_managed_roots,
            "protected_paths": list(OVERLAY_PROTECTED_PATHS),
            "schema_version": 1,
        }:
            raise RuntimeError("finished release overlay contract is invalid")

        if manifest.get("platform") == "macos":
            launcher_info = file_infos.get("启动题库系统.command")
            if launcher_info is None:
                raise RuntimeError("finished macOS release has no launcher")
            unix_mode = launcher_info.external_attr >> 16
            if launcher_info.create_system == 3 and not unix_mode & 0o111:
                raise RuntimeError("finished macOS launcher is not executable")
        else:
            if WINDOWS_LAUNCHER_HELPER not in names:
                raise RuntimeError(
                    "finished Windows release has no launcher helper: "
                    + WINDOWS_LAUNCHER_HELPER
                )
            runtime_members = {
                *(f"python/{name}" for name in MSVC_RUNTIME_DLLS),
                f"python/{MSVC_RUNTIME_NOTICE_NAME}",
            }
            missing_runtime = sorted(runtime_members - names)
            if missing_runtime:
                raise RuntimeError(
                    "finished Windows release is missing app-local VC Runtime: "
                    + ", ".join(missing_runtime)
                )
            launcher_info = file_infos.get(WINDOWS_LAUNCHER_NAME)
            if launcher_info is None:
                raise RuntimeError("finished Windows release has no launcher")
            if launcher_info.file_size > 1024 * 1024:
                raise RuntimeError("finished Windows launcher is unexpectedly large")
            with zip_ref.open(launcher_info, "r") as launcher_stream:
                launcher_payload = launcher_stream.read(1024 * 1024 + 1)
            if len(launcher_payload) > 1024 * 1024:
                raise RuntimeError("finished Windows launcher is unexpectedly large")
            _validate_windows_launcher_bytes(
                launcher_payload, "finished Windows launcher"
            )

        entries = manifest.get("files")
        if not isinstance(entries, list) or len(entries) > 100_000:
            raise RuntimeError("finished release manifest file list is invalid")
        expected_files = {}
        expected_collision_keys = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("finished release manifest contains an invalid entry")
            name = entry.get("path")
            expected_size = entry.get("size")
            expected_hash = entry.get("sha256")
            if not isinstance(name, str) or name == "RELEASE-MANIFEST.json":
                raise RuntimeError("finished release manifest contains an invalid path")
            member_path = PurePosixPath(name)
            if (
                "\\" in name
                or member_path.is_absolute()
                or member_path.as_posix() != name
                or any(part in {"", ".", ".."} or ":" in part for part in member_path.parts)
                or name.casefold() in expected_collision_keys
            ):
                raise RuntimeError(
                    f"finished release manifest contains a duplicate or unsafe path: {name}"
                )
            if type(expected_size) is not int or expected_size < 0:
                raise RuntimeError(
                    f"finished release manifest has an invalid size for: {name}"
                )
            if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(
                expected_hash
            ):
                raise RuntimeError(
                    f"finished release manifest has an invalid SHA-256 for: {name}"
                )
            expected_files[name] = (expected_size, expected_hash)
            expected_collision_keys.add(name.casefold())

        expected_names = set(expected_files) | {"RELEASE-MANIFEST.json"}
        if names != expected_names:
            missing_from_zip = sorted(expected_names - names)
            extra_in_zip = sorted(names - expected_names)
            raise RuntimeError(
                "finished release archive does not match its manifest: "
                f"missing={missing_from_zip[:10]}, extra={extra_in_zip[:10]}"
            )

        for name, (expected_size, expected_hash) in expected_files.items():
            info = file_infos[name]
            if info.file_size != expected_size:
                raise RuntimeError(
                    f"finished release member size does not match manifest: {name}"
                )
            actual_size = 0
            digest = hashlib.sha256()
            with zip_ref.open(info, "r") as member_stream:
                for chunk in iter(lambda: member_stream.read(1024 * 1024), b""):
                    actual_size += len(chunk)
                    if actual_size > expected_size:
                        raise RuntimeError(
                            f"finished release member exceeds manifest size: {name}"
                        )
                    digest.update(chunk)
            if actual_size != expected_size or not hmac.compare_digest(
                digest.hexdigest(), expected_hash
            ):
                raise RuntimeError(
                    f"finished release member SHA-256 does not match manifest: {name}"
                )


def _write_archive_checksum(zip_path):
    zip_path = Path(zip_path)
    checksum_path = zip_path.with_name(zip_path.name + ".sha256")
    _atomic_write_text(
        checksum_path, f"{sha256_file(zip_path)}  {zip_path.name}\n"
    )
    return checksum_path


def _build_archive(staging_root, archive_stem, platform_name, launcher_name):
    archive_path = Path(f"{archive_stem}.zip")
    checksum_path = archive_path.with_name(archive_path.name + ".sha256")
    archive_path.unlink(missing_ok=True)
    checksum_path.unlink(missing_ok=True)
    try:
        assert_release_tree_clean(staging_root, platform_name)
        if platform_name == "windows-x64":
            validate_windows_launcher(Path(staging_root, WINDOWS_LAUNCHER_NAME))
        validate_staged_python_sources(staging_root)
        write_release_manifest(staging_root, platform_name)
        archive_path = Path(shutil.make_archive(archive_stem, "zip", staging_root))
        required_members = {
            "RELEASE-MANIFEST.json",
            "main.py",
            launcher_name,
            "scripts/release_overlay.py",
            "scripts/local_launcher.py",
            "覆盖升级说明.txt",
        }
        if platform_name == "windows-x64":
            required_members.add(WINDOWS_LAUNCHER_HELPER)
            required_members.update(
                f"python/{name}" for name in MSVC_RUNTIME_DLLS
            )
            required_members.add(f"python/{MSVC_RUNTIME_NOTICE_NAME}")
        verify_zip_archive(archive_path, required_members)
        checksum_path = _write_archive_checksum(archive_path)
    except Exception:
        archive_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        raise
    print(f"[ok] Release archive created: {archive_path}")
    print(f"[ok] Archive checksum created: {checksum_path}")
    return os.fspath(archive_path)


def zip_release():
    print("[build] Zipping Windows release package...")
    validate_windows_runtime(BUILD_DIR)
    return _build_archive(
        BUILD_DIR,
        os.path.join(DIST_DIR, "StarHope-Windows-x64"),
        "windows-x64",
        WINDOWS_LAUNCHER_NAME,
    )


def cleanup_temp():
    print("[build] Cleaning up temporary files...")
    shutil.rmtree(WHEELS_DIR, ignore_errors=True)
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    shutil.rmtree(PORTABLE_BUILD_DIR, ignore_errors=True)


def main():
    try:
        # Clear stale finals before even the earliest preflight.  A failed
        # invocation must never leave an older package looking newly valid.
        _remove_release_outputs()
        validate_release_source()
        validate_target_requirements()
        clean_directories()
        build_portable_release()
        cleanup_temp()
        print("[ok] Portable Release package built successfully.")
    except Exception as exc:
        # One archive is the release set.  Remove known outputs while
        # preserving every unrelated dist file.
        _remove_release_outputs()
        print(f"[error] Error during packaging: {exc}")
        raise


if __name__ == "__main__":
    main()
