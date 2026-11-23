"""Android LAN security and durable inbox checks; no production database or AI."""
import io
import threading
import uuid

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from starhope.mobile_link import MobileLink, private_ipv4


def image():
    buffer = io.BytesIO()
    Image.new('RGB', (64, 48), 'white').save(buffer, 'JPEG')
    return buffer.getvalue()


@pytest.fixture
def link(tmp_path):
    clock = [100.0]
    library = lambda query, page: {'items': [{'id': 1, 'content': '合成题 $x+1$', 'answer_markdown': '2'}], 'total': 1, 'next_page': None}
    result = MobileLink(library, tmp_path / 'inbox', clock=lambda: clock[0])
    result.port = 8765
    result.rotate_code()
    result.test_clock = clock
    return result


def client(link, **kwargs):
    return TestClient(link.mobile_app, base_url='http://192.168.1.10:8765', client=('192.168.1.11', 1234), **kwargs)


def pair(link, connection):
    response = connection.post('/api/pair', json={'code': link.code})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}


def test_unpaired_clients_cannot_read_or_upload_and_no_admin_routes(link):
    with client(link) as c:
        assert c.get('/').status_code == 200
        assert 'no-store' in c.get('/').headers['cache-control']
        assert c.get('/api/library').status_code == 401
        assert c.post('/api/photos?identity=' + str(uuid.uuid4()), files={'file': ('x.jpg', image())}).status_code == 401
        headers = pair(link,c)
        assert c.get('/api/library', headers=headers).json()['items'][0]['id'] == 1
        assert c.get('/api/library?page=0', headers=headers).status_code == 422
        assert c.get('/api/settings', headers=headers).status_code == 404
        assert c.get('/static/index.html', headers=headers).status_code == 404
        assert c.get('/api/mobile-link', headers=headers).status_code == 404
        assert c.post('/api/shutdown', headers=headers).status_code == 404
        assert c.get('/api/connection', headers={'X-Local-Token': 'not-a-session'}).status_code == 401


@pytest.mark.parametrize('missing', ('mobile.html', 'js/mobile.js', 'css/mobile.css'))
def test_incomplete_mobile_assets_fail_before_start_and_return_actionable_error(link, tmp_path, monkeypatch, missing):
    from starhope import mobile_link
    assets = tmp_path / 'static'
    for name in mobile_link.MOBILE_ASSETS:
        path = assets / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic asset', encoding='utf-8')
    (assets / missing).unlink()
    monkeypatch.setattr(mobile_link, 'STATIC_DIR', assets)
    with pytest.raises(HTTPException) as error:
        link.start()
    assert error.value.status_code == 503
    assert '重新完整解压' in error.value.detail
    assert link.server is None and not link.inbox.exists()
    with client(link, raise_server_exceptions=False) as c:
        for url in ('/', '/mobile.js', '/mobile.css'):
            response = c.get(url)
            assert response.status_code == 503
            assert '手机页面资源不完整' in response.json()['detail']
            assert 'no-store' in response.headers['cache-control']


def test_host_peer_and_origin_cannot_be_spoofed(link):
    with client(link) as c:
        assert c.get('/', headers={'Host':'evil.example:8765'}).status_code == 403
        assert c.get('/', headers={'Host':'192.168.1.10:8766'}).status_code == 403
        assert c.post('/api/pair', json={'code':link.code}, headers={'Origin':'https://evil.example'}).status_code == 403
        assert c.get('/api/library', headers={'X-Forwarded-For':'127.0.0.1'}).status_code == 401
    with TestClient(link.mobile_app,base_url='http://192.168.1.10:8765',client=('8.8.8.8',1)) as c:
        assert c.get('/',headers={'X-Forwarded-For':'192.168.1.2'}).status_code == 403
    assert private_ipv4('10.0.0.2') and private_ipv4('172.16.0.2')
    assert not private_ipv4('100.64.0.2') and not private_ipv4('169.254.0.2')


def test_code_throttling_expiry_rotation_and_session_revocation(link):
    with client(link) as c:
        for code in ('wrong', '一二三四五六七八', '１２３４５６７８', {}, '0000000'):
            assert c.post('/api/pair', json={'code':code}).status_code == 403
        link.rotate_code()
        assert c.post('/api/pair',json={'code':link.code}).status_code == 429
        link.test_clock[0] += 61
        headers = pair(link,c)
        old = link.code
        link.rotate_code()
        if old != link.code:
            assert c.post('/api/pair',json={'code':old}).status_code == 403
        assert c.get('/api/connection',headers=headers).status_code == 200
        link.test_clock[0] += 601
        assert c.post('/api/pair',json={'code':link.code}).status_code == 403
        link.test_clock[0] += 12*3600
        assert c.get('/api/connection',headers=headers).status_code == 401
        link.rotate_code(); headers=pair(link,c)
        link.close()
        assert c.get('/api/connection',headers=headers).status_code == 401


def test_normalized_idempotent_inbox_survives_disconnect_and_restart(link):
    with client(link) as c:
        headers=pair(link,c); identity=str(uuid.uuid4()); raw=image()
        url='/api/photos?identity='+identity
        response=c.post(url,headers=headers,files={'file':('../../evil.html',raw,'text/html')})
        assert response.status_code == 200,response.text
        row=response.json()
        assert row['extension']=='.jpg' and row['media_type']=='image/jpeg'
        assert c.post(url,headers=headers,files={'file':('x.jpg',raw)}).json()==row
        assert c.post(url,headers=headers,files={'file':('x.jpg',raw+b'changed')}).status_code==409
        assert c.post('/api/photos?identity=../bad',headers=headers,files={'file':('x.jpg',raw)}).status_code==400
        assert c.post('/api/photos?identity='+str(uuid.uuid4()),headers=headers,files={'file':('x.svg',b'<svg/>')}).status_code==400
        link.close()
        assert len(link.list_photos())==1
        replacement=MobileLink(link.read_library,link.inbox)
        assert replacement.list_photos()==[row]
        metadata,path=replacement.photo(identity)
        with Image.open(path) as picture: assert picture.size==(64,48)
        assert path.read_bytes()!=raw  # Server re-encoding, no original client filename.
        replacement.remove(identity)
        assert replacement.list_photos()==[]


def test_desktop_inbox_thumbnail_is_bounded_jpeg_and_identity_checked(link):
    from fastapi import FastAPI
    desktop = TestClient(FastAPI())
    desktop.app.include_router(link.desktop_router())
    buffer = io.BytesIO()
    Image.new('RGB', (640, 480), 'white').save(buffer, 'PNG')
    row = link.accept(str(uuid.uuid4()), buffer.getvalue())
    response = desktop.get(f'/api/mobile-link/photos/{row["id"]}/thumb')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'image/jpeg'
    with Image.open(io.BytesIO(response.content)) as picture:
        assert picture.format == 'JPEG'
        assert max(picture.size) <= 360
    assert desktop.get('/api/mobile-link/photos/not-a-uuid/thumb').status_code == 400


def test_lan_photo_body_upload_gets_more_than_thirty_seconds():
    from starhope import mobile_link
    assert mobile_link.UPLOAD_BODY_TIMEOUT_SECONDS >= 120


def test_request_size_and_memory_concurrency_are_bounded(link):
    with client(link) as c:
        headers=pair(link,c)
        assert c.post('/api/pair',content=b' '*1025).status_code==413
        assert c.post('/api/photos',headers={**headers,'Content-Length':str(100*1024*1024)},content=b'x').status_code==413
        assert link.upload_slots.acquire(False) and link.upload_slots.acquire(False)
        try:
            assert c.post('/api/pair',json={'code':link.code}).status_code==429
        finally:
            link.upload_slots.release(); link.upload_slots.release()


def test_concurrent_replays_create_one_receipt_and_full_inbox_preserves_every_photo(link):
    raw=image(); identity=str(uuid.uuid4()); results=[]
    threads=[threading.Thread(target=lambda:results.append(link.accept(identity,raw))) for _ in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert len(results)==4 and len(link.list_photos())==1
    for _ in range(19): link.accept(str(uuid.uuid4()),raw)
    with pytest.raises(HTTPException) as error: link.accept(str(uuid.uuid4()),raw)
    assert error.value.status_code==409 and len(link.list_photos())==20
    # A repeated successful submission still succeeds when the inbox is full.
    assert link.accept(identity,raw)==results[0]


def test_symlinked_photo_or_corrupt_receipt_is_not_followed(link,tmp_path):
    row=link.accept(str(uuid.uuid4()),image()); _,path=link.photo(row['id'])
    path.unlink(); external=tmp_path/'other'; external.write_text('private')
    path.symlink_to(external)
    with pytest.raises(HTTPException): link.photo(row['id'])
    assert external.read_text()=='private'
    path.unlink()
    (link.inbox/(row['id']+'.json')).write_text('bad')
    with pytest.raises(HTTPException): link.list_photos()


def test_disconnect_revokes_only_its_session_and_pairing_has_a_device_cap(link):
    with client(link) as c:
        first=pair(link,c); second=pair(link,c)
        assert c.post('/api/disconnect',headers=first,content=b'').status_code==200
        assert c.get('/api/connection',headers=first).status_code==401
        assert c.get('/api/connection',headers=second).status_code==200
        for _ in range(9): pair(link,c)
        assert c.post('/api/pair',json={'code':link.code}).status_code==409


def test_changed_image_and_oversized_receipt_are_rejected(link):
    import json
    row=link.accept(str(uuid.uuid4()),image()); _,path=link.photo(row['id'])
    path.write_bytes(b'changed')
    with pytest.raises(HTTPException): link.photo_bytes(row['id'])
    receipt=link.inbox/(row['id']+'.json')
    row['size']=1024**4;receipt.write_text(json.dumps(row))
    with pytest.raises(HTTPException): link.list_photos()


def test_crash_between_image_and_receipt_can_resume_and_symlink_root_is_rejected(link,tmp_path):
    from starhope.asset_security import normalize_raster_image
    raw=image();identity=str(uuid.uuid4());normalized=normalize_raster_image(raw)
    link.inbox.mkdir();(link.inbox/(identity+normalized.extension)).write_bytes(normalized.data)
    assert link.accept(identity,raw)['id']==identity
    other=tmp_path/'linked';other.symlink_to(link.inbox,target_is_directory=True)
    unsafe=MobileLink(link.read_library,other)
    with pytest.raises(HTTPException):unsafe.list_photos()
