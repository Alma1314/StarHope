"""Real scan tasks and SQLite; all model calls are replaced with fixtures."""
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from starhope import learning_scans as scans
from starhope.database import Base, Learner, LearningAttempt, Question
from starhope.db_migrations import LATEST_SCHEMA_VERSION
from starhope.learning import create_learning_router, profile_basis
from starhope.pdf_ocr_capacity import PdfOcrCapacity
from starhope.question_duplicate_service import fingerprint_for_question, upsert_question_fingerprint


def photo():
    image = Image.new('RGB', (400, 600), 'white')
    draw = ImageDraw.Draw(image); draw.rectangle((80, 80, 220, 250), outline='black', width=3)
    draw.text((20, 350), 'student x=3', fill='blue')
    output = io.BytesIO(); image.save(output, 'PNG'); return output.getvalue()


def recognized(**changes):
    return {'number': '1', 'start_page': 1, 'end_page': 1, 'content': '已知$x=1$，求$x+1$。',
        'student_answer': '$1+1=3$', 'printed_answer': '', 'teacher_mark': '',
        'question_type': 'detailed_answer', 'topics': ['一元一次方程'], 'recognition': 'clear',
        'complete': True, 'figures_required': False, 'figures': [], **changes}


def grade(**changes):
    return {'record_id': '1', 'outcome': 'incorrect', 'certainty': 'clear', 'reference_answer': '$1+1=2$',
        'error_types': ['calculation'], 'evidence': '$1+1=3$', 'reason': '学生将1+1算成3。',
        'suggestion': '复核加法运算，再练一道不同题。', **changes}


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "learning.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with engine.begin() as c: c.exec_driver_sql(f'PRAGMA user_version={LATEST_SCHEMA_VERSION}')
    factory = sessionmaker(bind=engine)
    updates, calls = [], []
    service = scans.LearningScanService(uploads_dir=tmp_path / 'uploads', url_prefix='static/uploads',
        root=tmp_path / 'scans', updater=SimpleNamespace(queue=updates.append), capacity=PdfOcrCapacity())
    provider = SimpleNamespace(api_key='fixture', supports_image_input=True, model_name='fixture', provider_code='fixture',
        api_base='https://fixture.invalid', chat_completions_url='https://fixture.invalid/chat/completions')
    monkeypatch.setattr(scans, 'resolve_ocr_provider', lambda *_: provider)
    monkeypatch.setattr(scans, 'resolve_text_provider', lambda *_: provider)
    monkeypatch.setattr(scans, 'text_provider_supports_images', lambda *_: True)
    monkeypatch.setattr(scans, 'apply_model_thinking_policy', lambda payload, **_: payload)
    replies = {'scan': {'questions': [recognized()], 'warnings': []}, 'grades': {'items': [grade()]}}
    def post(_provider, payload, **kwargs):
        calls.append((deepcopy(payload), kwargs))
        packet = replies['scan' if payload['messages'][0]['content'] == scans.SCAN_PROMPT else 'grades']
        return SimpleNamespace(json=lambda: {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(packet)}}]})
    monkeypatch.setattr(scans, 'post_chat_completion', post)
    def get_db():
        with factory() as db: yield db
    app = FastAPI(); app.include_router(create_learning_router(get_db)); app.include_router(service.router(get_db))
    with factory() as db:
        learner = Learner(name='不得发送的昵称', auto_profile=True); db.add(learner); db.flush(); owner = learner.id
        for n in (1, 2, 3):
            q = Question(content=f'已知$x={n}$，求$x+1$。', answer_markdown=f'${n + 1}$', subject='数学',
                education_stage='高中', subject_topics='一元一次方程', question_type='detailed_answer', difficulty='normal')
            db.add(q); db.flush()
            upsert_question_fingerprint(db, q, fingerprint_for_question(q, uploads_dir=service.uploads, url_prefix=service.prefix))
        db.commit()
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, factory=factory, service=service, owner=owner, updates=updates, calls=calls, replies=replies, root=tmp_path)
    service.tasks.shutdown(wait=True); engine.dispose()


def submit(env, identifier=None, files=None):
    identifier = identifier or str(uuid.uuid4())
    response = env.client.post(f'/api/learning/learners/{env.owner}/scan-tasks',
        data={'request_id': identifier, 'subject': '数学', 'education_stage': '高中'},
        files=files or [('files', ('作业.png', photo(), 'image/png'))])
    assert response.status_code == 200, response.text
    future = env.service.tasks._records[identifier].future
    if future: future.result(timeout=10)
    state = env.client.get(f'/api/learning/learners/{env.owner}/scan-tasks/{identifier}').json()
    assert state['status'] == 'completed', state
    return identifier, state


def apply(env, identifier, **changes):
    payload = {'items': [{'record_id': '1', **changes}]}
    response = env.client.post(f'/api/learning/learners/{env.owner}/scan-tasks/{identifier}/apply', json=payload)
    return response, payload


def test_scan_reuses_question_stores_evidence_updates_profile_and_recommends(env):
    identifier, state = submit(env)
    assert len(env.calls) == 2 and str(env.root) not in json.dumps(state)
    assert '不得发送的昵称' not in json.dumps(env.calls, ensure_ascii=False)
    assert all(not k['retry_connection'] and not k['allow_redirects'] for _, k in env.calls)
    assert env.calls[0][1]['timeout'] == scans.DOCUMENT_AI_TIMEOUT_SECONDS
    assert env.calls[1][1]['timeout'] == scans.AI_REQUEST_TIMEOUT_SECONDS
    with env.factory() as db: assert db.query(LearningAttempt).count() == 0
    response, payload = apply(env, identifier)
    assert response.status_code == 200, response.text
    row_id = response.json()['items'][0]['id']
    with env.factory() as db:
        assert db.query(Question).count() == 3
        row = db.get(LearningAttempt, row_id); assessment = json.loads(row.assessment)
        assert row.question_id == 1 and row.outcome == 'incorrect'
        assert assessment['error_types'] == ['calculation'] and not assessment['human_reviewed']
        assert Path(env.service.uploads, Path(assessment['evidence_images'][0]).name).is_file()
        assert profile_basis(db, db.get(Learner, env.owner))['recent_attempts'][0]['assessment_source'] == 'scan_ai'
    assert env.updates == [env.owner]
    detail = env.client.get(f'/api/learning/learners/{env.owner}').json()
    assert detail['automatic_graded'] == 1 and detail['incorrect'] == 1 and detail['error_types'][0]['type'] == 'calculation'
    recommended = env.client.get(f'/api/learning/learners/{env.owner}/recommendations?education_stage=高中&subject=数学')
    assert recommended.status_code == 200, recommended.text
    assert recommended.json()['items'][0]['evidence_ids'] == [row_id]
    path = f'/api/learning/learners/{env.owner}/scan-tasks/{identifier}/apply'
    assert env.client.post(path, json=payload).json()['replayed']
    env.service.tasks.remove(identifier)  # Also replay after a process loses staged state.
    assert env.client.post(path, json=payload).json()['replayed']
    with env.factory() as db: assert db.query(LearningAttempt).count() == 1


def test_manual_correction_removes_mistake_preserves_ai_evidence(env):
    identifier, _ = submit(env); response, _ = apply(env, identifier); row_id = response.json()['items'][0]['id']
    path = f'/api/learning/learners/{env.owner}/attempts/{row_id}'
    response = env.client.patch(path, json={'revision': 1, 'outcome': 'correct', 'notes': '复核误读', 'error_types': []})
    assert response.status_code == 200, response.text
    assert response.json()['assessment']['original_grade']['outcome'] == 'incorrect'
    assert response.json()['assessment']['human_reviewed']
    assert env.client.get(f'/api/learning/learners/{env.owner}/mistakes').json()['total'] == 0
    recommended = env.client.get(f'/api/learning/learners/{env.owner}/recommendations?education_stage=高中').json()
    assert 1 not in [row['id'] for row in recommended['items']]


@pytest.mark.parametrize('change', [{'recognition': 'review'}, {'complete': False}, {'student_answer': ''}, {'figures_required': True}])
def test_uncertainty_does_not_pollute_mastery(env, change):
    env.replies['scan']['questions'] = [recognized(**change)]
    identifier, state = submit(env)
    assert state['records'][0]['grade']['outcome'] == 'ungraded'
    response, _ = apply(env, identifier)
    assert response.status_code == 200 and response.json()['items'][0]['outcome'] == 'ungraded'
    assert env.client.get(f'/api/learning/learners/{env.owner}').json()['mastery'] == []


@pytest.mark.parametrize('changes', [{'content': '修正题目，求$x+2$。', 'outcome': 'correct'}, {'outcome': 'correct'}])
def test_edits_invalidate_ai_judgement_without_manual_confirmation(env, changes):
    identifier, _ = submit(env)
    response, _ = apply(env, identifier, **changes)
    assert response.status_code == 200 and response.json()['items'][0]['outcome'] == 'ungraded'
    identifier, _ = submit(env)
    response, _ = apply(env, identifier, content='人工核对题目，求$x+3$。', outcome='correct', human_reviewed=True)
    assert response.status_code == 200 and response.json()['items'][0]['outcome'] == 'correct'


def test_grading_failure_keeps_recognition_without_retry(env):
    env.replies['grades'] = {'items': [grade(evidence='学生没写过的步骤')]}
    identifier, state = submit(env)
    assert state['records'][0]['grade']['outcome'] == 'ungraded' and state['warnings']
    assert len(env.calls) == 2 and apply(env, identifier)[0].status_code == 200


def test_confirmed_crop_enters_question_original_page_stays_personal(env):
    env.replies['scan']['questions'] = [recognized(content='新图题，求面积。[[figure:1]]', figures_required=True,
        figures=[{'page': 1, 'bbox': [150, 100, 650, 450]}])]
    identifier, _ = submit(env); response, _ = apply(env, identifier, include_figures=True)
    assert response.status_code == 200, response.text
    with env.factory() as db:
        row = db.get(LearningAttempt, response.json()['items'][0]['id']); q = db.get(Question, row.question_id)
        evidence = json.loads(row.assessment)['evidence_images']
        assert q.answer_markdown == '' and row.reference_answer == '$1+1=2$'
        assert len(q.image_paths) == 1 and evidence[0] not in q.content and q.image_paths[0] not in evidence


def test_owner_idempotency_and_input_binding(env):
    identifier, _ = submit(env); submit(env, identifier=identifier)
    assert len(env.calls) == 2
    other = env.client.post('/api/learning/learners', json={'name': '另一个档案'}).json()['id']
    path = f'/api/learning/learners/{other}/scan-tasks/{identifier}'
    assert env.client.get(path).status_code == 404
    assert env.client.get(path + '/images/page-1').status_code == 404
    assert env.client.post(path + '/apply', json={'items': [{'record_id': '1'}]}).status_code == 404
    assert env.client.delete(path).status_code == 404
    response = env.client.post(f'/api/learning/learners/{env.owner}/scan-tasks', data={
        'request_id': identifier, 'subject': '物理'}, files=[('files', ('different.png', photo(), 'image/png'))])
    assert response.status_code == 409


def test_tampered_page_rejected_and_import_transaction_rolled_back(env):
    env.replies['scan']['questions'] = [recognized(content='准备回滚的新题，求$x+5$。')]
    identifier, _ = submit(env); env.service._file(identifier, 'page-1.jpg').write_bytes(photo())
    assert apply(env, identifier)[0].status_code == 400
    with env.factory() as db: assert db.query(Question).count() == 3 and db.query(LearningAttempt).count() == 0
    assert not list(env.service.uploads.glob('*.jpg'))


def test_scanned_pdf_preserves_cross_page_evidence(env):
    import pymupdf
    document = pymupdf.open(); document.new_page(); document.new_page(); data = document.tobytes(); document.close()
    env.replies['scan']['questions'] = [recognized(end_page=2)]
    identifier, state = submit(env, files=[('files', ('试卷.pdf', data, 'application/pdf'))])
    assert len(state['pages']) == 2
    response, _ = apply(env, identifier)
    assert response.status_code == 200, response.text
    with env.factory() as db: assert len(json.loads(db.query(LearningAttempt).one().assessment)['evidence_images']) == 2


def test_recommendations_follow_stage_and_subject(env):
    identifier, _ = submit(env); apply(env, identifier)
    assert env.client.get(f'/api/learning/learners/{env.owner}/recommendations?education_stage=初中').json()['items'] == []
    assert env.client.get(f'/api/learning/learners/{env.owner}/recommendations?subject=物理').json()['items'] == []
    assert env.client.get(f'/api/learning/learners/{env.owner}/recommendations?limit=100').status_code == 400


def test_scan_free_topic_can_recommend_a_confirmed_structured_point(env):
    from starhope.database import KnowledgePoint, QuestionKnowledgePoint
    with env.factory() as db:
        point = KnowledgePoint(code='fixture:equation', name='一元一次方程', original_name='一元一次方程', source='local')
        db.add(point)
        question = Question(content='结构化知识点的新练习', subject='数学', education_stage='高中', subject_topics='', difficulty='normal')
        db.add(question); db.flush()
        db.add(QuestionKnowledgePoint(question_id=question.id, point_code=point.code, role='main', status='confirmed', position=0))
        identifier = question.id; db.commit()
    scan_id, _ = submit(env); apply(env, scan_id)
    result = env.client.get(f'/api/learning/learners/{env.owner}/recommendations?education_stage=高中&subject=数学')
    assert result.status_code == 200
    assert identifier in [row['id'] for row in result.json()['items']]


def test_changed_bank_answer_invalidates_the_old_automatic_judgement(env):
    env.service.session_factory = env.factory
    identifier, state = submit(env)
    assert state['records'][0]['bank_question_id'] == 1
    assert state['records'][0]['bank_reference'] == '$2$'
    with env.factory() as db:
        db.get(Question, 1).answer_markdown = '修正后的原答案'; db.commit()
    response, _ = apply(env, identifier)
    assert response.status_code == 200 and response.json()['items'][0]['outcome'] == 'ungraded'


def test_unknown_commit_preserves_images_and_replays_without_duplicate(env, monkeypatch):
    from sqlalchemy.orm import Session
    identifier, _ = submit(env)
    commit = Session.commit
    def committed_but_ack_lost(db):
        commit(db)
        raise OSError('simulated lost commit acknowledgement')
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', committed_but_ack_lost)
        response, payload = apply(env, identifier)
    assert response.status_code == 503
    with env.factory() as db:
        assert db.query(LearningAttempt).count() == 1
        path = json.loads(db.query(LearningAttempt).one().assessment)['evidence_images'][0]
    assert (env.service.uploads / Path(path).name).is_file()
    replay = env.client.post(f'/api/learning/learners/{env.owner}/scan-tasks/{identifier}/apply', json=payload)
    assert replay.status_code == 200 and replay.json()['replayed']
    with env.factory() as db: assert db.query(LearningAttempt).count() == 1


def test_full_backup_includes_scan_evidence_question_export_does_not(env):
    from starhope.backup import create_full_backup, verify_full_backup
    from starhope.sync_helper import serialize_exchange_questions
    identifier, _ = submit(env); apply(env, identifier)
    archive = create_full_backup(database_path=env.root / 'learning.db', uploads_dir=env.service.uploads,
        metadata_path=env.root / 'custom_metadata.json', output_dir=env.root / 'backups', retention=None)
    assert verify_full_backup(archive)['database']['row_counts']['learning_attempts'] == 1
    with env.factory() as db:
        exported = json.dumps(serialize_exchange_questions(db, [db.get(Question, 1)], 'A'), ensure_ascii=False)
        assessment = json.loads(db.query(LearningAttempt).one().assessment)
        assert assessment['evidence_images'][0] not in exported
        assert '$1+1=3$' not in exported and '不得发送的昵称' not in exported


def test_cancel_keeps_real_http_capacity_until_request_finishes(env, monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    post = scans.post_chat_completion
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return post(*args, **kwargs)
    monkeypatch.setattr(scans, 'post_chat_completion', blocked)
    identifier = str(uuid.uuid4())
    response = env.client.post(f'/api/learning/learners/{env.owner}/scan-tasks',
        data={'request_id': identifier, 'subject': '数学', 'education_stage': '高中'},
        files=[('files', ('cancel.png', photo(), 'image/png'))])
    assert response.status_code == 200 and entered.wait(3)
    future = env.service.tasks._records[identifier].future
    try:
        assert env.client.delete(f'/api/learning/learners/{env.owner}/scan-tasks/{identifier}').status_code == 200
        assert env.service.capacity.snapshot()['active'] == 1
        assert env.service.tasks.snapshot(identifier)['status'] == 'cancelled'
    finally:
        release.set(); future.result(timeout=5)
    assert env.service.capacity.snapshot()['active'] == 0 and len(env.calls) == 1
    with env.factory() as db: assert db.query(LearningAttempt).count() == 0


def test_schema22_backup_and_failed_assessment_migration_preserve_records(tmp_path, monkeypatch):
    from starhope import db_migrations
    from starhope.backup import create_full_backup, verify_full_backup
    engine = create_engine(f'sqlite:///{tmp_path / "old.db"}')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        learner = Learner(name='旧档案', auto_profile=False); db.add(learner); db.flush()
        db.add(LearningAttempt(id=str(uuid.uuid4()), learner_id=learner.id, request_hash='a' * 64,
            question_identity='b' * 64, content='原题文', reference_answer='原答案', subject='数学',
            difficulty='normal', topics='[]', student_answer='原学生作答', outcome='incorrect', origin='manual', notes='原核对备注'))
        db.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql('ALTER TABLE learning_attempts DROP COLUMN assessment')
        connection.exec_driver_sql('PRAGMA user_version=22')
    monkeypatch.setattr(db_migrations, 'SCHEMA_SNAPSHOT_DIR', tmp_path / 'snapshots')
    uploads = tmp_path / 'uploads'; uploads.mkdir()
    archive = create_full_backup(database_path=tmp_path / 'old.db', uploads_dir=uploads,
        metadata_path=tmp_path / 'custom_metadata.json', output_dir=tmp_path / 'backups', retention=None)
    assert verify_full_backup(archive)['database']['schema_version'] == 22
    validate = db_migrations._validate_learning_schema
    def reject(connection, **kwargs):
        validate(connection, **kwargs)
        raise RuntimeError('injected assessment validation failure')
    with monkeypatch.context() as patch:
        patch.setattr(db_migrations, '_validate_learning_schema', reject)
        with pytest.raises(RuntimeError, match='injected assessment'):
            db_migrations.migrate_database(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA user_version').scalar_one() == 22
        assert 'assessment' not in {row[1] for row in connection.exec_driver_sql('PRAGMA table_info(learning_attempts)')}
    result = db_migrations.migrate_database(engine)
    assert result['from_version'] == 22 and result['to_version'] == LATEST_SCHEMA_VERSION and Path(result['backup']).is_file()
    with engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT content, student_answer, outcome, notes, assessment FROM learning_attempts').one() == (
            '原题文', '原学生作答', 'incorrect', '原核对备注', '{}')
    engine.dispose()
