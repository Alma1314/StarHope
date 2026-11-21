"""Run with the existing API fixtures in a disposable source checkout."""
import pytest
from starhope.database import Question


@pytest.fixture(autouse=True)
def auth(client):
    from main import LOCAL_TOKEN
    client.headers.update({'X-Local-Token': LOCAL_TOKEN})


def payload(**extra):
    return {'content': '一个物体受到恒力作用，求加速度。', 'question_type': 'detailed_answer',
            'difficulty': 'normal', 'subject': '物理', 'subject_topics': '牛顿第二定律', **extra}


def test_nonmath_save_filter_and_legacy_client_preservation(client, db_session):
    saved = client.post('/api/questions', data=payload())
    assert saved.status_code == 200, saved.text
    q = saved.json()['question']; qid = q['id']
    assert q['subject'] == '物理' and q['subject_topics'] == '牛顿第二定律'
    data = payload(content='一个物体质量为2，受到恒力，求加速度。')
    data.pop('subject'); data.pop('subject_topics')
    response = client.put(f'/api/questions/{qid}', data=data)
    assert response.status_code == 200, response.text
    assert response.json()['question']['subject'] == '物理'
    assert response.json()['question']['subject_topics'] == '牛顿第二定律'
    physics = client.get('/api/questions', params={'subject': '物理', 'page': 1}).json()
    assert [row['id'] for row in physics['items']] == [qid]
    assert client.get('/api/questions', params={'subject': '数学', 'page': 1}).json()['total'] == 0
    response = client.put(f'/api/questions/{qid}', data=payload(subject_topics=''))
    assert response.status_code == 200, response.text
    assert response.json()['question']['subject_topics'] == ''


def test_default_math_and_reject_invalid_subject(client, db_session):
    data = payload(); data.pop('subject'); data.pop('subject_topics')
    response = client.post('/api/questions', data=data)
    assert response.status_code == 200, response.text
    assert response.json()['question']['subject'] == '数学'
    response = client.post('/api/questions', data=payload(subject='invalid'))
    assert response.status_code == 400
    assert db_session.query(Question).count() == 1
