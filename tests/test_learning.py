"""Local learning records use isolated SQLite and model replacements only."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from starhope.database import Base, Learner, LearningAttempt, LearningReview, Question
from starhope import learning


@pytest.fixture
def isolated(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'learning.sqlite'), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    def get_db():
        with factory() as db: yield db
    app = FastAPI(); app.include_router(learning.create_learning_router(get_db))
    with factory() as db:
        db.add_all([Question(content=f'第{i}道力学题', answer_markdown=f'原答案{i}', subject='物理',
            subject_topics='牛顿第二定律', difficulty='normal') for i in range(1, 5)])
        db.commit()
    with TestClient(app) as client:
        learner = client.post('/api/learning/learners', json={'name': '昵称甲', 'grade': '高一', 'goal': '巩固力学'}).json()
        yield client, factory, engine, learner, tmp_path
    engine.dispose()


def record(env, qid=1, *, owner=None, **changes):
    client, _, _, learner, _ = env
    snapshot = client.get(f'/api/learning/practice/{qid}').json()
    payload = {'request_id': str(uuid.uuid4()), 'question_id': qid, 'question_revision': snapshot['question_revision'],
               'origin': 'practice', 'student_answer': '我的解题步骤', 'elapsed_seconds': 40, **changes}
    response = client.post(f"/api/learning/learners/{owner or learner['id']}/attempts", json=payload)
    assert response.status_code == 200, response.text
    return response.json(), payload


def test_practice_records_then_manual_grading_updates_only_its_learner(isolated):
    client, factory, _, learner, _ = isolated
    assert 'reference_answer' not in client.get('/api/learning/practice/1').json()
    with factory() as db:
        db.get(Question, 1).question_type = 'multi_choice'; db.commit()
    assert client.get('/api/learning/practice/1').json()['question_type'] == 'multi_choice'
    other = client.post('/api/learning/learners', json={'name': '昵称乙'}).json()
    row, _ = record(isolated)
    assert row['reference_answer'] == '原答案1'
    detail = client.get(f"/api/learning/learners/{learner['id']}").json()
    assert detail['attempts'] == detail['ungraded'] == 1 and detail['mastery'] == []
    response = client.patch(f"/api/learning/learners/{learner['id']}/attempts/{row['id']}",
                            json={'revision': row['revision'], 'outcome': 'partial', 'notes': '老师核对'})
    assert response.status_code == 200
    detail = client.get(f"/api/learning/learners/{learner['id']}").json()
    assert detail['mastery'][0]['score_rate'] == 50 and detail['mastery'][0]['status'] == '证据不足'
    assert client.get(f"/api/learning/learners/{other['id']}").json()['attempts'] == 0
    assert client.get(f"/api/learning/learners/{other['id']}/attempts/{row['id']}").status_code == 404
    assert client.patch(f"/api/learning/learners/{other['id']}/attempts/{row['id']}", json={'revision': 1, 'outcome': 'correct'}).status_code == 404
    with factory() as db: assert db.query(LearningAttempt).count() == 1


def test_repeat_same_question_uses_latest_grade_not_another_independent_sample(isolated):
    client, _, _, learner, _ = isolated
    for qid, outcome in [(1, 'incorrect'), (1, 'correct'), (1, 'correct'), (2, 'partial'), (3, 'incorrect')]:
        record(isolated, qid, origin='manual', outcome=outcome)
    detail = client.get(f"/api/learning/learners/{learner['id']}").json()
    topic = detail['mastery'][0]
    assert topic['sample_count'] == 3 and topic['attempt_count'] == 5
    assert topic['score_rate'] == 50 and topic['status'] == '练习中'
    assert len(topic['evidence_ids']) == 3


def test_unknown_knowledge_remains_unassessed(isolated):
    client, factory, _, learner, _ = isolated
    with factory() as db:
        db.get(Question, 1).subject_topics = ''; db.commit()
    record(isolated, origin='manual', outcome='correct')
    detail = client.get(f"/api/learning/learners/{learner['id']}").json()
    assert detail['graded'] == 1 and detail['mastery'] == []
    assert client.post(f"/api/learning/learners/{learner['id']}/profile-tasks", json={'request_id': str(uuid.uuid4())}).status_code == 400


def test_idempotent_record_and_stale_snapshot_and_grade_checks(isolated):
    client, factory, _, learner, _ = isolated
    row, payload = record(isolated)
    path = f"/api/learning/learners/{learner['id']}/attempts"
    assert client.post(path, json=payload).json()['id'] == row['id']
    assert client.post(path, json={**payload, 'student_answer': '不同作答'}).status_code == 409
    with factory() as db:
        q = db.get(Question, 1); q.content = '修改后的题目'; q.subject_topics = '动量'; db.commit()
    assert client.post(path, json={**payload, 'request_id': str(uuid.uuid4())}).status_code == 409
    # The replay belongs to the original immutable question, even after editing.
    assert client.post(path, json=payload).json()['content'] == '第1道力学题'
    assert client.patch(path + '/' + row['id'], json={'revision': 99, 'outcome': 'correct'}).status_code == 409
    with factory() as db:
        assert db.query(LearningAttempt).count() == 1 and db.get(LearningAttempt, row['id']).outcome == 'ungraded'


@pytest.mark.parametrize('change', [{'question_id': True}, {'elapsed_seconds': -1}, {'elapsed_seconds': 1.5},
    {'student_answer': 'x' * 10001}, {'outcome': 'correct'}, {'origin': 'unknown'}, {'request_id': 'not-uuid'}])
def test_invalid_record_is_not_written(isolated, change):
    client, factory, _, learner, _ = isolated
    snapshot = client.get('/api/learning/practice/1').json()
    response = client.post(f"/api/learning/learners/{learner['id']}/attempts", json={
        'request_id': str(uuid.uuid4()), 'question_id': 1, 'question_revision': snapshot['question_revision'],
        'student_answer': '作答', **change})
    assert response.status_code == 400
    with factory() as db: assert db.query(LearningAttempt).count() == 0


def test_question_deletion_preserves_history_and_learner_delete_cascades(isolated):
    client, factory, _, learner, _ = isolated
    row, _ = record(isolated)
    with factory() as db:
        db.execute(text('DELETE FROM questions WHERE id=1')); db.commit()
    history = client.get(f"/api/learning/learners/{learner['id']}/attempts/{row['id']}").json()
    assert history['question_id'] is None and history['reference_answer'] == '原答案1'
    assert history['content'] == '第1道力学题'
    detail = client.get(f"/api/learning/learners/{learner['id']}").json()
    assert client.delete(f"/api/learning/learners/{learner['id']}?revision=0").status_code == 409
    assert client.delete(f"/api/learning/learners/{learner['id']}?revision={detail['revision']}").status_code == 200
    with factory() as db: assert db.query(LearningAttempt).count() == db.query(Learner).count() == 0


def test_ai_profile_is_evidenced_readonly_until_confirmed_and_rejects_stale(isolated, monkeypatch):
    client, factory, _, learner, _ = isolated
    row, _ = record(isolated, origin='manual', outcome='incorrect')
    summary = client.get(f"/api/learning/learners/{learner['id']}").json()
    basis = {'revision': summary['revision'], 'graded': 1, 'mastery': summary['mastery']}
    value = {'summary': '只有一道题，证据不足。', 'insights': [{'topic_key': basis['mastery'][0]['key'],
        'observation': '本次记录为错误，尚不能认定长期掌握程度。', 'action': '再做两道不同题，并记录解题步骤。',
        'error_pattern': '步骤不足，暂不能定位具体错误原因。',
        'evidence_ids': [row['id']]}], 'study_plan': ['先复习受力分析，再练习不同条件的题目。']}
    sent = []
    def post(provider, payload, **kwargs):
        sent.append((deepcopy(payload), kwargs))
        return SimpleNamespace(json=lambda: {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]})
    monkeypatch.setattr(learning, 'post_chat_completion', post)
    monkeypatch.setattr(learning, 'apply_model_thinking_policy', lambda payload, **kwargs: payload)
    identifier = str(uuid.uuid4()); learning.PROFILE_TASKS.create(identifier, learner_id=learner['id'])
    learning.run_profile(identifier, basis, SimpleNamespace(model_name='fixture'))
    state = learning.PROFILE_TASKS.snapshot(identifier)
    assert state['status'] == 'completed', state
    assert sent[0][1]['retry_connection'] is False
    path = f"/api/learning/learners/{learner['id']}/profile-tasks/{identifier}/apply"
    with factory() as db: assert db.get(Learner, learner['id']).ai_profile == '{}'
    assert client.post(path).status_code == 200
    assert client.get(f"/api/learning/learners/{learner['id']}").json()['profile_stale'] is False
    record(isolated, 2, origin='manual', outcome='correct')
    assert client.post(path).status_code == 409
    assert client.get(f"/api/learning/learners/{learner['id']}").json()['profile_stale'] is True
    value['insights'][0]['evidence_ids'] = ['invented-record']
    bad = str(uuid.uuid4()); learning.PROFILE_TASKS.create(bad, learner_id=learner['id'])
    learning.run_profile(bad, basis, SimpleNamespace(model_name='fixture'))
    assert learning.PROFILE_TASKS.snapshot(bad)['status'] == 'error'


def test_profile_task_freezes_limited_input_omits_name_and_reconnects_without_resubmission(isolated, monkeypatch):
    client, _, _, learner, _ = isolated
    record(isolated, origin='manual', outcome='incorrect')
    dispatched = []
    monkeypatch.setattr(learning, 'resolve_text_provider', lambda _: SimpleNamespace(api_key='fixture', model_name='fixture'))
    monkeypatch.setattr(learning.PROFILE_TASKS, 'submit', lambda *args: dispatched.append(args))
    identifier = str(uuid.uuid4()); path = f"/api/learning/learners/{learner['id']}/profile-tasks"
    assert client.post(path, json={'request_id': identifier}).status_code == 200
    assert client.post(path, json={'request_id': identifier}).json()['task_id'] == identifier
    assert len(dispatched) == 1
    basis = dispatched[0][3]
    assert 'name' not in basis and '昵称甲' not in json.dumps(basis, ensure_ascii=False)
    assert 'reference_answer' not in basis['recent_attempts'][0]
    assert basis['mastery'][0]['sample_count'] == 1
    assert client.post(path + '/' + identifier + '/cancel').status_code == 200


def test_automatic_profile_discards_stale_result_and_corrects_from_latest_record(isolated, monkeypatch):
    import threading
    from starhope.task_manager import TaskManager
    client, factory, _, learner, _ = isolated
    row, _ = record(isolated, origin='manual', outcome='incorrect')
    manager = TaskManager(max_workers=1, max_queue=2)
    monkeypatch.setattr(learning, 'PROFILE_TASKS', manager)
    monkeypatch.setattr(learning, 'profile_provider', lambda: SimpleNamespace(api_key='fixture', model_name='fixture'))
    monkeypatch.setattr(learning, 'apply_model_thinking_policy', lambda payload, **kwargs: payload)
    first_started, release_first, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def post(provider, payload, **kwargs):
        basis = json.loads(payload['messages'][1]['content']); calls.append(basis)
        if len(calls) == 1:
            first_started.set(); assert release_first.wait(5)
        corrected = basis['recent_attempts'][0]['outcome'] == 'correct'
        report = {'summary': '已按新核对记录订正' if corrected else '旧错误记录', 'insights': [{
            'topic_key': basis['mastery'][0]['key'], 'observation': '依据实际人工核对记录',
            'error_pattern': '本题已订正，尚需不同题验证' if corrected else '本次错误，原因待核对',
            'action': '再练习两道不同题', 'evidence_ids': basis['mastery'][0]['evidence_ids']}], 'study_plan': []}
        return SimpleNamespace(json=lambda: {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(report)}}]})
    monkeypatch.setattr(learning, 'post_chat_completion', post)
    updater = learning.AutoProfileUpdater(factory, debounce_seconds=0)
    original = updater._work
    def completed(*args):
        original(*args)
        with factory() as db:
            person = db.get(Learner, learner['id']); profile = json.loads(person.ai_profile)
            if profile.get('basis_revision') == person.revision and profile['report']['summary'] == '已按新核对记录订正': finished.set()
    monkeypatch.setattr(updater, '_work', completed)
    try:
        updater.queue(learner['id']); assert first_started.wait(5)
        response = client.patch(f"/api/learning/learners/{learner['id']}/attempts/{row['id']}",
            json={'revision': row['revision'], 'outcome': 'correct', 'notes': '老师已核对订正步骤'})
        assert response.status_code == 200
        updater.queue(learner['id']); release_first.set()
        assert finished.wait(5), 'latest automatic correction was not persisted'
        assert len(calls) == 2 and calls[-1]['recent_attempts'][0]['outcome'] == 'correct'
        assert '老师已核对' in calls[-1]['recent_attempts'][0]['notes_excerpt']
        with factory() as db:
            person = db.get(Learner, learner['id']); profile = json.loads(person.ai_profile)
            assert profile['basis_revision'] == person.revision and profile['updated_by'] == 'automatic'
    finally:
        release_first.set(); updater.close(); manager.shutdown(wait=True)


def test_migration_and_full_backup_keep_learners_and_snapshot_images(isolated, monkeypatch):
    client, factory, engine, learner, root = isolated
    uploads = root / 'uploads'; uploads.mkdir()
    Image.new('RGB', (4, 4), 'white').save(uploads / 'figure.png')
    with factory() as db:
        db.get(Question, 1).content = '原题 ![](/static/uploads/figure.png)'; db.commit()
    record(isolated, origin='manual', outcome='correct')
    with factory() as db:
        db.execute(text('DELETE FROM questions WHERE id=1')); db.commit()
    from starhope import db_migrations
    with engine.begin() as conn: conn.exec_driver_sql(f'PRAGMA user_version={db_migrations.LATEST_SCHEMA_VERSION}')
    db_migrations.migrate_database(engine)
    from starhope.backup import create_full_backup, verify_full_backup
    archive = create_full_backup(database_path=Path(engine.url.database), uploads_dir=uploads,
        output_dir=root / 'backups', metadata_path=root / 'custom_metadata.json')
    manifest = verify_full_backup(archive)
    assert manifest['database']['row_counts']['learners'] == 1
    assert manifest['database']['row_counts']['learning_attempts'] == 1
    assert manifest['database']['row_counts']['learning_reviews'] == 1
    assert manifest['database']['referenced_uploads'] == ['uploads/figure.png']
    # Exercise the actual v17 upgrade separately: no implicit schema writes.
    with engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE learning_attempts'); conn.exec_driver_sql('DROP TABLE learning_reviews')
        conn.exec_driver_sql('DROP TABLE learners')
        conn.exec_driver_sql('PRAGMA user_version=17')
    monkeypatch.setattr(db_migrations, 'SCHEMA_SNAPSHOT_DIR', root / 'schema')
    result = db_migrations.migrate_database(engine)
    assert result['to_version'] == db_migrations.LATEST_SCHEMA_VERSION and Path(result['backup']).is_file()
    with engine.connect() as conn:
        assert conn.exec_driver_sql('SELECT COUNT(*) FROM questions').scalar_one() == 3
        assert conn.exec_driver_sql('SELECT COUNT(*) FROM learners').scalar_one() == 0


def test_mistakes_track_latest_state_and_mastery(isolated):
    client, _, _, learner, _ = isolated
    record(isolated, 1, origin='manual', outcome='incorrect')
    record(isolated, 2, origin='manual', outcome='partial')
    record(isolated, 1, origin='manual', outcome='partial')
    result = client.get(f"/api/learning/learners/{learner['id']}/mistakes").json()
    assert result['total'] == 2 and {row['question_id'] for row in result['items']} == {1, 2}
    assert next(row for row in result['items'] if row['question_id'] == 1)['wrong_count'] == 2
    detail = client.get(f"/api/learning/learners/{learner['id']}").json()
    assert detail['open_mistakes'] == 2 and detail['mastered'] == 0
    record(isolated, 1, origin='manual', outcome='correct')
    result = client.get(f"/api/learning/learners/{learner['id']}/mistakes").json()
    assert result['total'] == 1 and result['items'][0]['question_id'] == 2
    detail = client.get(f"/api/learning/learners/{learner['id']}").json()
    assert detail['open_mistakes'] == 1 and detail['mastered'] == 1


def test_review_schedule_records_wrong_then_extends_on_correct(isolated):
    client, factory, _, learner, _ = isolated
    record(isolated, 1, origin='manual', outcome='incorrect')
    assert client.get(f"/api/learning/learners/{learner['id']}/review").json() == {'total': 0, 'items': []}
    with factory() as db:
        review = db.query(LearningReview).one()
        assert review.interval_days == 1 and review.streak == 0 and review.due_at > review.updated_at
        review.due_at = review.updated_at; db.commit()
    due = client.get(f"/api/learning/learners/{learner['id']}/review").json()
    assert due['total'] == 1 and due['items'][0]['question_id'] == 1
    assert client.get(f"/api/learning/learners/{learner['id']}").json()['review_due'] == 1
    record(isolated, 1, origin='manual', outcome='correct')
    with factory() as db:
        review = db.query(LearningReview).one()
        assert review.interval_days == 2 and review.streak == 1
        review.due_at = review.updated_at; db.commit()
    due = client.get(f"/api/learning/learners/{learner['id']}/review").json()
    assert due['total'] == 1 and due['items'][0]['interval_days'] == 2


def test_explanation_is_advice_only_and_requires_wrong_grade(isolated, monkeypatch):
    client, factory, _, learner, _ = isolated
    row, _ = record(isolated, origin='manual', outcome='incorrect')
    value = {'error_summary': '符号写反', 'error_point': '第二行移项时未变号',
             'suggestion': '移项后逐行检查符号', 'variant': '同类一元一次方程'}
    monkeypatch.setattr(learning, 'profile_provider', lambda: SimpleNamespace(api_key='fixture', model_name='fixture'))
    monkeypatch.setattr(learning, 'apply_model_thinking_policy', lambda payload, **kwargs: payload)
    monkeypatch.setattr(learning, 'post_chat_completion', lambda provider, payload, **kwargs:
        SimpleNamespace(json=lambda: {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}))
    result = client.post(f"/api/learning/learners/{learner['id']}/attempts/{row['id']}/explain").json()
    assert result['error_point'] == value['error_point'] and result['attempt_id'] == row['id']
    with factory() as db: assert db.get(LearningAttempt, row['id']).notes == ''
    correct, _ = record(isolated, 2, origin='manual', outcome='correct')
    assert client.post(f"/api/learning/learners/{learner['id']}/attempts/{correct['id']}/explain").status_code == 400
