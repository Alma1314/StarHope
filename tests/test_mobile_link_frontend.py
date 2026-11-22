"""Execute the editor guard and HTTP-safe UUID logic without a browser or model."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run(source):
    result = subprocess.run([shutil.which('node'), '-'], input=source, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_phone_ocr_waits_for_existing_unsaved_editor_guard():
    source = (ROOT/'static/js/editor.js').read_text()
    start = source.index('function openNewQuestionEditor(onReady)')
    function = source[start:source.index("document.addEventListener('keydown'",start)]
    run("""
const assert=require('node:assert/strict');let allowed=false, opened=0, loaded=0;
const EditorState={questionId:42},window={},modal={dataset:{}},title={textContent:''};
const document={getElementById(id){return id==='editorSection'?modal:title;}};
function checkAndSwitch(callback){if(allowed)callback();}
function startNewQuestion(){}function setQuestionDetailEditAvailability(){}
function openQuestionEditorModal(){opened++;}
""" + function + """
openNewQuestionEditor(()=>loaded++);assert.equal(loaded,0);assert.equal(opened,0);
allowed=true;openNewQuestionEditor(()=>loaded++);assert.equal(loaded,1);assert.equal(opened,1);
assert.equal(modal.dataset.returnQuestionId,'42');
openNewQuestionEditor();assert.equal(loaded,1);assert.equal(opened,2);
""")


def test_camera_uuid_works_without_https_random_uuid():
    source=(ROOT/'static/js/mobile.js').read_text()
    start=source.index('const bytes = crypto.getRandomValues')
    end=source.index('preview = URL.createObjectURL(file)',start)
    run("const assert=require('node:assert/strict');let identity;\n"+source[start:end]+"\nassert.match(identity,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);")


def test_repeat_qr_scan_reuses_connected_page_without_repairing():
    source = (ROOT/'static/js/mobile.js').read_text()
    start = source.index('async function connectQr(ticket)')
    function = source[start:source.index('// Scanners that reuse', start)]
    run("""
const assert=require('node:assert/strict');
let token='existing', pairing=false, limit=0, paired=0, shown=null, said='';
let request=async path=>{if(path!=='/api/connection')throw new Error(path);return{max_image_bytes:123};};
const connected=value=>{shown=value;};const say=value=>{said=value;};
const $=()=>({focus(){}});const pairDevice=async()=>{paired++;};
""" + function + """
(async()=>{
  await connectQr('used-ticket');
  assert.equal(paired,0);assert.equal(limit,123);assert.equal(shown,true);
  assert.equal(said,'本页已连接电脑，无需重复扫码。');
  token='stale';request=async()=>{throw new Error('已失效');};
  await connectQr('used-ticket');
  assert.equal(paired,1);assert.equal(said,'正在扫码连接电脑…');
})().catch(error=>{console.error(error);process.exit(1);});
""")


def test_learning_photo_handoff_checks_busy_and_preserves_existing_files_on_cancel():
    source=(ROOT/'static/js/learning.js').read_text()
    start=source.index('receivePhoto: file => {')
    end=source.index(', open: () => {',start)
    function='const receivePhoto='+source[start+len('receivePhoto: '):end]+';'
    run("""
const assert=require('node:assert/strict');let initialized=true,saving=true,accept=false,panel='';
const original={keep:true};const input={files:[original],focus(){}};const status={textContent:''};
const $=id=>id==='learningScanFiles'?input:status;const showToast=()=>{};
const showPanel=value=>{panel=value;};const confirm=()=>accept;
class DataTransfer{constructor(){this.files=[];this.items={add:file=>this.files.push(file)};}}
"""+function+"""
const file={phone:true};assert.equal(receivePhoto(file),false);assert.equal(input.files[0],original);
saving=false;assert.equal(receivePhoto(file),false);assert.equal(input.files[0],original);
accept=true;assert.equal(receivePhoto(file),true);assert.equal(input.files[0],file);assert.equal(panel,'scan');
""")
