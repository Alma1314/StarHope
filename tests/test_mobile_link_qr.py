"""QR credentials and independent decoding; optional system ZBar is test-only."""
import ctypes
import ctypes.util
import io
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from test_mobile_link import link, client


def decode_qr(raw):
    library = ctypes.util.find_library('zbar')
    if not library:
        pytest.skip('Optional system ZBar decoder is unavailable')
    api = ctypes.CDLL(library)
    pointer = ctypes.c_void_p
    signatures = {
        'zbar_image_scanner_create': (pointer, []),
        'zbar_image_create': (pointer, []),
        'zbar_image_set_format': (None, [pointer, ctypes.c_ulong]),
        'zbar_image_set_size': (None, [pointer, ctypes.c_uint, ctypes.c_uint]),
        'zbar_image_set_data': (None, [pointer, pointer, ctypes.c_ulong, pointer]),
        'zbar_scan_image': (ctypes.c_int, [pointer, pointer]),
        'zbar_image_first_symbol': (pointer, [pointer]),
        'zbar_symbol_get_data': (pointer, [pointer]),
        'zbar_symbol_get_data_length': (ctypes.c_uint, [pointer]),
        'zbar_image_destroy': (None, [pointer]),
        'zbar_image_scanner_destroy': (None, [pointer]),
    }
    for name,(result,args) in signatures.items():
        function=getattr(api,name);function.restype=result;function.argtypes=args
    scanner=api.zbar_image_scanner_create();image=api.zbar_image_create()
    try:
        with Image.open(io.BytesIO(raw)) as picture:
            gray=picture.convert('L')
        pixels=ctypes.create_string_buffer(gray.tobytes())
        api.zbar_image_set_format(image,int.from_bytes(b'Y800','little'))
        api.zbar_image_set_size(image,*gray.size)
        api.zbar_image_set_data(image,pixels,gray.width*gray.height,None)
        assert api.zbar_scan_image(scanner,image)==1, 'QR image must decode to exactly one symbol'
        symbol=api.zbar_image_first_symbol(image)
        return ctypes.string_at(api.zbar_symbol_get_data(symbol),api.zbar_symbol_get_data_length(symbol)).decode('utf-8')
    finally:
        api.zbar_image_destroy(image);api.zbar_image_scanner_destroy(scanner)


def running(link,monkeypatch):
    link.server=SimpleNamespace(started=True,should_exit=False)
    link.thread=SimpleNamespace(is_alive=lambda:True,join=lambda timeout:None)
    monkeypatch.setattr('starhope.mobile_link.local_addresses',lambda:['192.168.1.10','10.0.0.2'])


def test_generated_qr_decodes_and_exchanges_once_without_leaking_control_token(link,monkeypatch):
    running(link,monkeypatch)
    desktop=TestClient(FastAPI())
    desktop.app.include_router(link.desktop_router())
    response=desktop.get('/api/mobile-link/qr?address=192.168.1.10')
    assert response.status_code==200 and response.headers['content-type']=='image/png'
    assert 'no-store' in response.headers['cache-control']
    url=urlsplit(decode_qr(response.content))
    assert url.scheme=='http' and url.netloc=='192.168.1.10:8765' and not url.query
    ticket=parse_qs(url.fragment)['pair'][0]
    assert ticket==link.qr_ticket and link.code not in url.fragment
    assert link.status()['qr_revision']
    with client(link) as c:
        result=c.post('/api/pair',json={'ticket':ticket})
        assert result.status_code==200
        headers={'Authorization':'Bearer '+result.json()['token']}
        assert c.get('/api/connection',headers=headers).status_code==200
        assert c.post('/api/pair',json={'ticket':ticket}).status_code==403
        assert not link.status()['qr_revision']
        assert desktop.get('/api/mobile-link/qr?address=192.168.1.10').status_code==410
        # The manual code remains a usable fallback after QR consumption.
        assert c.post('/api/pair',json={'code':link.code}).status_code==200
        link.close()
        assert c.get('/api/connection',headers=headers).status_code==401


def test_qr_rotation_expiry_address_validation_and_failed_attempt_budget(link,monkeypatch):
    running(link,monkeypatch)
    old=link.qr_ticket;revision=link.qr_revision;link.rotate_code()
    assert old!=link.qr_ticket and revision!=link.qr_revision
    with client(link) as c:
        assert c.post('/api/pair',json={'ticket':old}).status_code==403
        for ticket in ('一'*32,[], 'x', 'x'*32):
            assert c.post('/api/pair',json={'ticket':ticket}).status_code==403
        assert c.post('/api/pair',json={'ticket':link.qr_ticket}).status_code==429
        link.test_clock[0]+=61
        link.rotate_code()
        link.test_clock[0]+=601
        assert c.post('/api/pair',json={'ticket':link.qr_ticket}).status_code==403
    with pytest.raises(HTTPException) as error:link.qr_png('8.8.8.8')
    assert error.value.status_code==400
    with pytest.raises(HTTPException) as error:link.qr_png('192.168.1.10')
    assert error.value.status_code==410
    assert not link.status()['qr_revision']


def test_default_address_prefers_the_lan_route_over_virtual_interfaces(monkeypatch):
    from starhope.mobile_link import local_addresses
    class Probe:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def connect(self,address):assert address==('192.168.1.1',9)
        def getsockname(self):return ('192.168.0.5',1234)
    monkeypatch.setattr('socket.getaddrinfo',lambda *args:[(None,None,None,None,(value,0)) for value in ('10.0.3.1','172.17.0.1','127.0.0.1')])
    monkeypatch.setattr('socket.socket',lambda *args:Probe())
    assert local_addresses()==['192.168.0.5','10.0.3.1','172.17.0.1']
