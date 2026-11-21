import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from starhope import database
from starhope.database import Base, Question

# Configure database module to use in-memory SQLite with StaticPool for tests
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Patch the database module's engine and SessionLocal
database.engine = test_engine
database.SessionLocal = TestSessionLocal


@pytest.fixture(scope="session", autouse=True)
def dispose_test_engine_after_session():
    yield
    test_engine.dispose()


@pytest.fixture(autouse=True)
def _forbid_paid_embeddings(monkeypatch):
    """Product save paths schedule embeddings; tests must never call a provider."""
    from starhope import semantic_index

    def forbidden(texts, config):
        raise RuntimeError("tests must not call a paid embedding endpoint")

    monkeypatch.setattr(semantic_index._INDEXER, "_embed", forbidden)
    yield
    semantic_index.get_indexer().close()


@pytest.fixture(autouse=True)
def _isolated_question_graph(monkeypatch, tmp_path):
    from starhope import question_graph

    graph = question_graph.QuestionGraph(tmp_path / "question_graph.db")
    monkeypatch.setattr(question_graph, "_GRAPH", graph)
    yield
    graph.close()


# Patch sync_helper output paths globally during testing to avoid overwriting or clearing real data_backup files
from starhope import sync_helper
import tempfile
import os
test_backup_dir = tempfile.mkdtemp()
sync_helper.BACKUP_DIR = test_backup_dir
sync_helper.JSON_BACKUP_PATH = os.path.join(test_backup_dir, "questions_backup.json")
sync_helper.MD_BACKUP_PATH = os.path.join(test_backup_dir, "questions_library.md")

@pytest.fixture(scope="function")
def db_session():
    # Create all tables in the in-memory database
    Base.metadata.create_all(bind=test_engine)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)

@pytest.fixture(scope="function")
def client(db_session):
    from fastapi.testclient import TestClient
    from main import app
    from starhope.database import get_db

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
