"""Application routes and snapshot images run only in disposable source copies."""
import uuid
from pathlib import Path

from PIL import Image


def test_learning_routes_and_snapshot_assets_survive_question_deletion(client, db_session):
    import main
    with main.LEARNING_UPDATER.factory() as session:
        from sqlalchemy import text
        assert session.execute(text('SELECT 1')).scalar_one() == 1
    client.headers['X-Local-Token'] = main.LOCAL_TOKEN
    image = Path(main.UPLOAD_DIR) / ('learning-' + uuid.uuid4().hex + '.png')
    Image.new('RGB', (4, 4), 'white').save(image)
    url = '/' + main.UPLOAD_DIR_REL + '/' + image.name
    question = client.post('/api/questions', data={'content': '受力图 ![](' + url + ')',
        'question_type': 'detailed_answer', 'answer_markdown': '原答案',
        'difficulty': 'normal', 'subject': '物理', 'subject_topics': '受力分析'}).json()['question']
    learner = client.post('/api/learning/learners', json={'name': '隔离学习档案', 'auto_profile': False}).json()
    snapshot = client.get('/api/learning/practice/' + str(question['id'])).json()
    response = client.post(f"/api/learning/learners/{learner['id']}/attempts", json={
        'request_id': str(uuid.uuid4()), 'question_id': question['id'], 'question_revision': snapshot['question_revision'],
        'student_answer': '隔离作答', 'origin': 'manual', 'outcome': 'incorrect'})
    assert response.status_code == 200, response.text
    attempt = response.json()
    assert image.resolve() in main._referenced_question_assets(db_session)

    response = client.delete('/api/questions/' + str(question['id']))
    assert response.status_code == 200, response.text
    row = client.get(f"/api/learning/learners/{learner['id']}/attempts/{attempt['id']}").json()
    assert row['question_id'] is None and url in row['content']
    assert client.get(url).status_code == 200
    assert image.resolve() in main._referenced_question_assets(db_session)


def test_scanned_answer_image_is_protected_without_becoming_question_content(client, db_session):
    import json
    import main
    from starhope.database import LearningAttempt
    client.headers['X-Local-Token'] = main.LOCAL_TOKEN
    image = Path(main.UPLOAD_DIR) / ('learning-evidence-' + uuid.uuid4().hex + '.png')
    Image.new('RGB', (4, 4), 'blue').save(image)
    url = '/' + main.UPLOAD_DIR_REL + '/' + image.name
    question = client.post('/api/questions', data={'content': '个人作答原图保护专项 $x=7$',
        'question_type': 'detailed_answer', 'difficulty': 'normal'}).json()['question']
    learner = client.post('/api/learning/learners', json={'name': '隔离扫描档案', 'auto_profile': False}).json()
    snapshot = client.get('/api/learning/practice/' + str(question['id'])).json()
    result = client.post(f"/api/learning/learners/{learner['id']}/attempts", json={
        'request_id': str(uuid.uuid4()), 'question_id': question['id'], 'question_revision': snapshot['question_revision'],
        'student_answer': '学生实际步骤', 'origin': 'manual', 'outcome': 'incorrect'})
    assert result.status_code == 200
    row = db_session.get(LearningAttempt, result.json()['id'])
    row.assessment = json.dumps({'source': 'scan_ai', 'evidence_images': [url], 'error_types': ['calculation']})
    db_session.commit()
    assert image.resolve() in main._referenced_question_assets(db_session)
    assert url not in question['content']
    assert client.delete('/api/questions/' + str(question['id'])).status_code == 200
    assert image.resolve() in main._referenced_question_assets(db_session)
    assert client.get(url).status_code == 200
