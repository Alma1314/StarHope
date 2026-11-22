"""Opt-in Android-size flow; native camera hardware still needs an Android device."""
import json
import base64
import uuid

from test_preview_browser import browser, pytestmark
from test_mobile_link_qr import decode_qr


def test_android_pair_photo_library_and_desktop_scan_handoff(browser, tmp_path):
    base = browser.evaluate('location.origin')
    status = None
    def wait(source):
        browser.command('wait', '--fn', source)
    try:
        browser.evaluate("document.getElementById('mobileLinkOpen').click();true")
        wait("document.getElementById('mobileLinkInbox').textContent.includes('暂无手机照片')")
        assert browser.evaluate("fetch('/api/mobile-link').then(r=>r.status)") == 403
        browser.evaluate("document.getElementById('mobileLinkStart').click();true")
        wait("document.getElementById('mobileLinkPin').textContent.length===8")
        status = browser.evaluate("fetch('/api/mobile-link',{headers:{'X-Local-Token':window.__localToken}}).then(r=>r.json())")
        assert status['enabled'] and status['urls']
        assert browser.evaluate("fetch('/api/mobile-link/qr?address=127.0.0.1').then(r=>r.status)") == 403
        wait("document.getElementById('mobileLinkQr').complete&&document.getElementById('mobileLinkQr').naturalWidth>0")
        encoded=browser.evaluate("fetch(document.getElementById('mobileLinkQr').src).then(r=>r.arrayBuffer()).then(bytes=>btoa(String.fromCharCode(...new Uint8Array(bytes))))")
        qr_url=decode_qr(base64.b64decode(encoded))
        browser.command('screenshot',str(tmp_path/'desktop-qr.png'))
        unique = '安卓合成题-' + uuid.uuid4().hex
        fixture = {'content': unique + ' $x+1=2$ <img src=x onerror=alert(1)>', 'subject':'物理',
                   'education_stage':'高中','question_type':'detailed_answer','difficulty':'medium', 'answer_markdown':'$x=1$'}
        assert browser.evaluate("""(async()=>{const form=new FormData();for(const [key,value] of Object.entries(""" + json.dumps(fixture) + """))form.append(key,value);return(await fetch('/api/questions',{method:'POST',body:form})).status;})()""") == 200
        browser.command('set', 'viewport', '393', '851')
        browser.command('open', qr_url)
        wait("document.body.dataset.mobileReady==='true'")
        wait("document.getElementById('pairPanel').hidden")
        assert browser.evaluate('location.hash') == ''
        assert browser.evaluate("document.getElementById('code').value") == ''
        assert not browser.evaluate('window.isSecureContext')
        assert browser.evaluate("document.getElementById('camera').getAttribute('capture')") == 'environment'
        assert browser.evaluate('document.documentElement.scrollWidth<=innerWidth')
        browser.evaluate("document.getElementById('bankTab').click();true")
        wait("document.getElementById('questions').textContent.includes(" + json.dumps(unique) + ")")
        assert browser.evaluate("document.getElementById('questions').querySelectorAll('img,script').length") == 0
        browser.evaluate("""document.getElementById('photoTab').click();(async()=>{const canvas=document.createElement('canvas');canvas.width=64;canvas.height=48;const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg'));const transfer=new DataTransfer();transfer.items.add(new File([blob],'synthetic-camera.jpg',{type:'image/jpeg'}));const camera=document.getElementById('camera');camera.files=transfer.files;camera.dispatchEvent(new Event('change'));return true;})()""")
        wait("!document.getElementById('send').disabled")
        browser.command('screenshot', str(tmp_path/'android-photo.png'))
        browser.evaluate("document.getElementById('send').click();true")
        wait("document.getElementById('receipts').children.length===1")
        assert browser.evaluate("document.getElementById('send').disabled")
        browser.evaluate("document.getElementById('disconnect').click();true")
        wait("!document.getElementById('pairPanel').hidden")
        browser.command('set', 'viewport', '1440', '1000')
        browser.command('open', base)
        wait("typeof LearningWorkspace==='object'")
        browser.evaluate("document.getElementById('mobileLinkOpen').click();true")
        wait("document.getElementById('mobileLinkInbox').textContent.includes('识别为新题')")
        final = browser.evaluate("fetch('/api/mobile-link',{headers:{'X-Local-Token':window.__localToken}}).then(r=>r.json())")
        assert len(final['photos']) == 1 and final['devices'] == 0
        browser.evaluate("[...document.getElementById('mobileLinkInbox').querySelectorAll('button')].find(b=>b.textContent==='用于已做题扫描').click();true")
        wait("document.getElementById('learningScanFiles').files.length===1")
        assert not browser.evaluate("document.querySelector('[data-learning-panel=scan]').hidden")
    finally:
        browser.command('open', base)
        wait("typeof LearningWorkspace==='object'")
        browser.evaluate("fetch('/api/mobile-link/stop',{method:'POST'}).then(r=>r.ok)")


def test_fresh_qr_rescan_in_open_page_reconnects(browser,tmp_path):
    import urllib.request, urllib.parse
    base=browser.evaluate('location.origin')
    def wait(source):browser.command('wait','--fn',source)
    try:
        browser.evaluate("document.getElementById('mobileLinkOpen').click();true")
        browser.evaluate("document.getElementById('mobileLinkStart').click();true")
        wait("document.getElementById('mobileLinkQr').complete&&document.getElementById('mobileLinkQr').naturalWidth>0")
        token=browser.evaluate('window.__localToken')
        def desktop(path,body=None):
            request=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),
                headers={'X-Local-Token':token},method='GET' if body is None else 'POST')
            with urllib.request.urlopen(request,timeout=10) as response:return response.read()
        status=json.loads(desktop('/api/mobile-link'));address=urllib.parse.urlsplit(status['urls'][0]).hostname
        first=decode_qr(desktop('/api/mobile-link/qr?address='+address))
        browser.command('set','viewport','393','851')
        browser.command('open',first)
        wait("document.body.dataset.mobileReady==='true'&&document.getElementById('pairPanel').hidden")
        browser.evaluate("document.getElementById('disconnect').click();true")
        wait("!document.getElementById('pairPanel').hidden")
        desktop('/api/mobile-link/code',{})
        second=decode_qr(desktop('/api/mobile-link/qr?address='+address))
        assert second!=first
        # The open page gets a new fragment without reloading, as a scanner reusing the tab would.
        browser.evaluate("location.hash='pair='+"+json.dumps(second.split('#pair=',1)[1])+";true")
        wait("document.getElementById('pairPanel').hidden")
        assert browser.evaluate('location.hash')==''
        assert not json.loads(desktop('/api/mobile-link'))['qr_revision']
        browser.command('screenshot',str(tmp_path/'android-open-page-rescan.png'))
    finally:
        browser.command('set','viewport','1440','1000')
        browser.command('open',base)
        wait("typeof LearningWorkspace==='object'")
        browser.evaluate("fetch('/api/mobile-link/stop',{method:'POST'}).then(r=>r.ok)")


def test_qr_address_selection_and_used_code_falls_back_to_manual(browser,tmp_path):
    base=browser.evaluate('location.origin')
    def wait(source):browser.command('wait','--fn',source)
    try:
        browser.evaluate("document.getElementById('mobileLinkOpen').click();true")
        browser.evaluate("document.getElementById('mobileLinkStart').click();true")
        wait("document.getElementById('mobileLinkQr').complete&&document.getElementById('mobileLinkQr').naturalWidth>0")
        status=browser.evaluate("fetch('/api/mobile-link',{headers:{'X-Local-Token':window.__localToken}}).then(r=>r.json())")
        chosen=status['urls'][-1]
        browser.evaluate("document.getElementById('mobileLinkAddress').value="+json.dumps(chosen)+";document.getElementById('mobileLinkAddress').dispatchEvent(new Event('change'));true")
        wait("document.getElementById('mobileLinkQr').complete&&document.getElementById('mobileLinkQr').naturalWidth>0")
        encoded=browser.evaluate("fetch(document.getElementById('mobileLinkQr').src).then(r=>r.arrayBuffer()).then(bytes=>btoa(String.fromCharCode(...new Uint8Array(bytes))))")
        url=decode_qr(base64.b64decode(encoded))
        assert url.startswith(chosen+'/#pair=')
        browser.command('set','viewport','393','851')
        browser.command('open',url)
        wait("document.body.dataset.mobileReady==='true'&&document.getElementById('pairPanel').hidden")
        assert browser.evaluate('location.hash')==''
        # Scanner reuses the already-open page: same fragment, no reload, must stay connected.
        ticket=url.split('#pair=',1)[1]
        browser.evaluate("location.hash='pair='+"+json.dumps(ticket)+";true")
        wait("document.getElementById('status').textContent.includes('无需重复扫码')")
        assert browser.evaluate("document.getElementById('pairPanel').hidden")
        assert browser.evaluate('location.hash')==''
        browser.evaluate("document.getElementById('disconnect').click();true")
        wait("!document.getElementById('pairPanel').hidden")
        browser.command('open',url)
        wait("document.body.dataset.mobileReady==='true'&&document.getElementById('status').textContent.includes('二维码已使用')")
        assert browser.evaluate('location.hash')==''
        assert not browser.evaluate("document.getElementById('pairPanel').hidden")
        browser.command('screenshot',str(tmp_path/'android-used-qr-fallback.png'))
        browser.evaluate("document.getElementById('code').value="+json.dumps(status['pairing_code'])+";document.getElementById('pairForm').requestSubmit();true")
        wait("document.getElementById('pairPanel').hidden")
    finally:
        browser.command('set','viewport','1440','1000')
        browser.command('open',base)
        browser.evaluate("fetch('/api/mobile-link/stop',{method:'POST'}).then(r=>r.ok)")
