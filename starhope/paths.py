"""Centralized, working-directory-independent paths for StarHope.

All persistent data and bundled resources are anchored to the project root so
the server and command-line tools behave identically regardless of the shell's
current working directory.
"""

import hashlib
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def project_instance_id(root: Path = PROJECT_ROOT) -> str:
    """Identify this checkout over localhost without disclosing its path.

    This is a routing hint, not an authentication credential or a PID identity.
    Resolving aliases keeps a symlinked launcher on the same library instance.
    """
    canonical = os.path.normcase(str(Path(root).resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

STATIC_DIR = PROJECT_ROOT / "static"
STATIC_JS_DIR = STATIC_DIR / "js"
STATIC_CSS_DIR = STATIC_DIR / "css"
UPLOADS_DIR = STATIC_DIR / "uploads"
TEST_UPLOADS_DIR = STATIC_DIR / "test_uploads"

TEMPLATES_DIR = PROJECT_ROOT / "templates"
DATA_BACKUP_DIR = PROJECT_ROOT / "data_backup"
FULL_BACKUP_DIR = DATA_BACKUP_DIR / "snapshots"
PRE_RESTORE_BACKUP_DIR = DATA_BACKUP_DIR / "pre_restore"
SCHEMA_SNAPSHOT_DIR = DATA_BACKUP_DIR / "schema_snapshots"
SYSTEM_GENERATED_DIR = PROJECT_ROOT / ".system_generated"
LOCAL_CONTENT_SOURCES_PATH = SYSTEM_GENERATED_DIR / "content_sources.local.json"
DOCUMENT_TASKS_DIR = SYSTEM_GENERATED_DIR / "document_tasks"
DOCUMENT_PROJECTS_DIR = SYSTEM_GENERATED_DIR / "document_projects"
RETAINED_UPLOADS_DIR = SYSTEM_GENERATED_DIR / "retained_uploads"
TIERED_STORAGE_DIR = SYSTEM_GENERATED_DIR / "tiered_storage"
TIERED_STORAGE_CONFIG = TIERED_STORAGE_DIR / "config.json"
RECEIVED_DOCUMENTS_DIR = SYSTEM_GENERATED_DIR / "received_documents"
EXPORT_PREFLIGHTS_DIR = SYSTEM_GENERATED_DIR / "export_preflights"
SEMANTIC_INDEX_FILE = SYSTEM_GENERATED_DIR / "semantic_index.sqlite"
LEARNING_SCAN_DIR = SYSTEM_GENERATED_DIR / "learning_scans"
MOBILE_INBOX_DIR = SYSTEM_GENERATED_DIR / "mobile_inbox"
QUESTION_GRAPH_FILE = SYSTEM_GENERATED_DIR / "question_graph.db"

DATABASE_FILE = PROJECT_ROOT / "starhope.db"
DATABASE_PATH = DATABASE_FILE
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE_FILE = PROJECT_ROOT / ".env.example"

DIST_DIR = PROJECT_ROOT / "dist"
BUILD_CACHE_DIR = PROJECT_ROOT / ".build_cache"
CURRICULUMS_DIR = Path(__file__).resolve().parent / "resources" / "curriculums"
KNOWLEDGE_PROMPT_PROFILES_DIR = PROJECT_ROOT / "starhope" / "resources" / "knowledge_prompt_profiles"


def project_path(*parts: str) -> Path:
    """Return an absolute path below the StarHope project root."""

    return PROJECT_ROOT.joinpath(*parts)


def sqlite_url(path: Path = DATABASE_FILE) -> str:
    """Build a cross-platform SQLAlchemy URL for an absolute SQLite path."""

    return f"sqlite:///{path.resolve().as_posix()}"
