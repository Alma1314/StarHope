"""Multi-subject proposals use isolated SQLite, no real models or user data."""
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from starhope.database import Base, Question
from starhope import subject_annotations as labels


@pytest.fixture
def isolated(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'test.sqlite'), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    def get_db():
        with factory() as db:
            yield db
    app = FastAPI()
    app.include_router(labels.create_subject_router(get_db, load_metadata=lambda: {'difficulties': [{'value': 'normal', 'label': '常规题'}]}, on_saved=lambda: None))
    with factory() as db:
        db.add_all([Question(content='求物体的加速度', difficulty='normal'), Question(content='求导数', difficulty='normal')])
        db.commit()
    with TestClient(app) as client:
        yield client, factory, engine
    engine.dispose()


def test_apply_is_explicit_and_atomic(isolated):
    client, factory, _ = isolated
    items = client.post('/api/subject-annotations/preview', json={'ids': [1, 2]}).json()['items']
    items[0].update(subject='物理', subject_topics='牛顿第二定律\n牛顿第二定律')
    with factory() as db:
        assert db.get(Question, 1).subject == '数学'
    items[1]['revision'] = 'stale'
    assert client.post('/api/subject-annotations/apply', json={'items': items}).status_code == 409
    with factory() as db:
        assert db.get(Question, 1).subject == '数学'
    items[1]['revision'] = client.post('/api/subject-annotations/preview', json={'ids': [2]}).json()['items'][0]['revision']
    response = client.post('/api/subject-annotations/apply', json={'items': items})
    assert response.status_code == 200, response.text
    with factory() as db:
        q = db.get(Question, 1)
        assert q.subject == '物理' and q.subject_topics == '牛顿第二定律'
        assert q.to_dict()['subject'] == '物理'
        assert q.to_summary_dict()['subject_topics'] == '牛顿第二定律'


@pytest.mark.parametrize('ids', [[], [True], [1, 1], list(range(1, 22)), ['1']])
def test_reject_invalid_batches(isolated, ids):
    assert isolated[0].post('/api/subject-annotations/preview', json={'ids': ids}).status_code == 400


def test_migration_preserves_old_questions_and_snapshots(isolated, tmp_path, monkeypatch):
    _, factory, engine = isolated
    with engine.begin() as conn:
        conn.execute(text('DROP INDEX ix_questions_subject'))
        conn.execute(text('ALTER TABLE questions DROP COLUMN subject'))
        conn.execute(text('ALTER TABLE questions DROP COLUMN subject_topics'))
        conn.execute(text('PRAGMA user_version=16'))
    from starhope.db_migrations import migrate_database
    import starhope.db_migrations as migrations
    monkeypatch.setattr(migrations, 'SCHEMA_SNAPSHOT_DIR', tmp_path / 'snapshots')
    result = migrate_database(engine)
    from pathlib import Path
    assert Path(result['backup']).is_file()
    assert result['to_version'] == 17
    with factory() as db:
        q = db.get(Question, 1)
        assert q.content == '求物体的加速度' and q.subject == '数学' and q.subject_topics == ''


def test_suggestions_are_readonly_and_strict(isolated, monkeypatch):
    client, factory, _ = isolated
    original = client.post('/api/subject-annotations/preview', json={'ids': [1]}).json()['items'][0]
    provider = SimpleNamespace(api_key='test', model_name='test')
    result = {'items': [{'id': 1, 'subject': '物理', 'difficulty': 'normal', 'subject_topics': '牛顿第二定律', 'reason': '题干要求加速度；常规力学运算'}]}
    monkeypatch.setattr(labels, 'post_chat_completion', lambda *a, **k: SimpleNamespace(json=lambda: {'choices': [{'message': {'content': json.dumps(result)}}]}))
    monkeypatch.setattr(labels, 'apply_model_thinking_policy', lambda data, **k: data)
    task_id = str(uuid.uuid4())
    labels.SUBJECT_TASKS.create(task_id)
    labels.run_suggestion(task_id, [original], provider, {'normal': '常规题'})
    state = labels.SUBJECT_TASKS.snapshot(task_id)
    assert state['status'] == 'completed' and state['items'][0]['subject'] == '物理'
    with factory() as db:
        assert db.get(Question, 1).subject == '数学'
    result['items'].append(result['items'][0])
    bad_id = str(uuid.uuid4()); labels.SUBJECT_TASKS.create(bad_id)
    labels.run_suggestion(bad_id, [original], provider, {'normal': '常规题'})
    assert labels.SUBJECT_TASKS.snapshot(bad_id)['status'] == 'error'


def test_validation_of_labels_and_levels(isolated):
    client, _, _ = isolated
    item = client.post('/api/subject-annotations/preview', json={'ids': [1]}).json()['items'][0]
    for patch in ({'subject': 'not-a-subject'}, {'subject_topics': 'a' * 2001}, {'difficulty': 'invented'}):
        assert client.post('/api/subject-annotations/apply', json={'items': [{**item, **patch}]}).status_code == 400


def test_subject_labels_survive_share_roundtrip(isolated, tmp_path):
    _, factory, _ = isolated
    from starhope.library_transfer import export_library_zip, import_library_zip
    uploads = tmp_path / 'uploads'; uploads.mkdir()
    package = tmp_path / 'share.zip'
    with factory() as db:
        q = db.get(Question, 1); q.subject = '物理'; q.subject_topics = '牛顿第二定律\n受力分析'
        db.commit()
        export_library_zip(db, package, uploads_dir=uploads, question_ids=[1])
    target = create_engine('sqlite:///' + str(tmp_path / 'target.sqlite'))
    Base.metadata.create_all(target)
    target_uploads = tmp_path / 'target_uploads'; target_uploads.mkdir()
    try:
        with sessionmaker(bind=target)() as db:
            imported = import_library_zip(db, package, uploads_dir=target_uploads)
            q = db.query(Question).one()
            assert q.subject == '物理' and q.subject_topics == '牛顿第二定律\n受力分析'
    finally:
        target.dispose()
