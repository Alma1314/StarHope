"""Educational stage isolation; run in a disposable source checkout."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from starhope.database import Base, Question
from starhope.subject_annotations import EDUCATION_STAGES


@pytest.fixture(autouse=True)
def local_auth(client):
    from main import LOCAL_TOKEN
    client.headers['X-Local-Token'] = LOCAL_TOKEN


def form(stage='高中', **kwargs):
    return dict(content='求函数的值。', question_type='detailed_answer', difficulty='normal',
                subject='物理', subject_topics='函数', education_stage=stage, **kwargs)


def test_saved_stage_is_validated_preserved_and_filtered_before_pagination(client, db_session):
    ids = {}
    for stage in EDUCATION_STAGES:
        response = client.post('/api/questions', data={**form(stage), 'content': f'{stage}函数专项：求函数的值。'})
        assert response.status_code == 200, response.text
        q = response.json()['question']; ids[stage] = q['id']
        assert q['education_stage'] == stage
    for stage, qid in ids.items():
        response = client.get('/api/questions', params={'education_stage': stage, 'q': '函数', 'page': 1, 'page_size': 1}).json()
        assert response['total'] == 1
        assert [(q['id'], q['education_stage']) for q in response['items']] == [(qid, stage)]
        assert [q['id'] for q in client.get('/api/questions', params={'education_stage': stage}).json()] == [qid]
    qid = ids['高中']
    old_client = form(); old_client.pop('education_stage')
    assert client.put(f'/api/questions/{qid}', data=old_client).json()['question']['education_stage'] == '高中'
    assert client.post('/api/questions', data={**old_client, 'content': '旧客户端新增函数专项。'}).json()['question']['education_stage'] == '未标注'
    assert client.post('/api/questions', data=form('高一')).status_code == 400
    assert client.get('/api/questions', params={'education_stage': 'invalid'}).status_code == 400
    # A direct display-number lookup still cannot escape the selected stage.
    result = client.get('/api/questions', params={'education_stage': '本科', 'q': '#1', 'page': 1}).json()
    assert result['items'] == []


def test_batch_stage_changes_require_fresh_confirmation(client, db_session):
    q = Question(content='求函数的值', difficulty='normal', education_stage='高中')
    db_session.add(q); db_session.commit()
    row = client.post('/api/subject-annotations/preview', json={'ids': [q.id]}).json()['items'][0]
    assert row['education_stage'] == '高中'
    assert client.post('/api/subject-annotations/apply', json={'items': [{**row, 'education_stage': '本科'}]}).status_code == 200
    assert client.post('/api/subject-annotations/apply', json={'items': [{**row, 'education_stage': '小学'}]}).status_code == 409
    fresh = client.post('/api/subject-annotations/preview', json={'ids': [q.id]}).json()['items'][0]
    assert client.post('/api/subject-annotations/apply', json={'items': [{**fresh, 'education_stage': 'invalid'}]}).status_code == 400
    assert db_session.get(Question, q.id).education_stage == '本科'


def test_ai_selection_and_fallback_never_cross_stage(client, db_session, monkeypatch):
    import main
    rows = [Question(content='函数求值', question_type='single_choice', difficulty='normal', education_stage=s)
            for s in ('初中', '高中', '本科', '未标注')]
    db_session.add_all(rows); db_session.commit()
    provider = SimpleNamespace(api_key='fake', api_base='https://example.invalid', model_name='fake',
        provider_label='test', credential_label='test', provider_code='test', reasoning_effort=None)
    monkeypatch.setattr(main, 'resolve_text_provider', lambda _: provider)
    # A malformed/out-of-scope model choice may trigger local supplementation.
    monkeypatch.setattr(main, 'post_chat_completion', lambda *a, **kw: SimpleNamespace(json=lambda: {
        'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'selected_ids': [rows[2].id]})}}]}))
    response = client.post('/api/paper/ai-select', json={'education_stage': '高中', 'reference_mode': 'none', 'limit': 3, 'prompt': '选3道函数题'})
    assert response.status_code == 200, response.text
    assert [q['id'] for q in response.json()['data']] == [rows[1].id]
    assert response.json()['selection_summary']['shortfall'] == 2
    assert client.post('/api/paper/ai-select', json={'education_stage': ['高中']}).status_code == 400


def test_v19_migration_snapshots_and_marks_legacy_unknown(tmp_path, monkeypatch):
    from starhope import db_migrations as migrations
    engine = create_engine('sqlite:///' + str(tmp_path / 'old.sqlite'))
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql('INSERT INTO questions(content) VALUES (?)', ('保留原题',))
        connection.exec_driver_sql('DROP INDEX ix_questions_education_stage')
        connection.exec_driver_sql('ALTER TABLE questions DROP COLUMN education_stage')
        connection.exec_driver_sql('PRAGMA user_version=19')
    monkeypatch.setattr(migrations, 'SCHEMA_SNAPSHOT_DIR', tmp_path / 'snapshots')
    result = migrations.migrate_database(engine)
    assert Path(result['backup']).is_file() and result['to_version'] == migrations.LATEST_SCHEMA_VERSION
    with sessionmaker(bind=engine)() as db:
        question = db.query(Question).one()
        assert question.education_stage == '未标注' and question.content == '保留原题'
    engine.dispose()


def test_profile_topic_evidence_is_separated_by_stage(db_session):
    from starhope.learning import question_snapshot
    a = Question(content='同名知识点', subject='数学', subject_topics='函数', education_stage='初中')
    b = Question(content='同名知识点', subject='数学', subject_topics='函数', education_stage='本科')
    db_session.add_all([a, b]); db_session.commit()
    assert question_snapshot(a)['topics'][0]['key'] != question_snapshot(b)['topics'][0]['key']
