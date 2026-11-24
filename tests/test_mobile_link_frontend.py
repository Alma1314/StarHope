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


def test_phone_pairs_as_connected_before_optional_connection_metadata():
    source = (ROOT/'static/js/mobile.js').read_text()
    start = source.index('async function pairDevice(payload)')
    function = source[start:source.index("$('pairForm').addEventListener", start)]
    run("""
const assert=require('node:assert/strict');
const DEFAULT_LIMIT=10485760;
let token='', pairing=false, limit=0, stored=null, shown=null, said='';
const $=id=>({value:'',disabled:false,querySelector:()=>({disabled:false})});
const sessionStorage={setItem:(key,value)=>{stored=value;},removeItem(){}};
let request=async path=>{if(path==='/api/pair')return{token:'issued'};throw new TypeError('Failed to fetch');};
const connected=value=>{shown=value;};const say=value=>{said=value;};
""" + function + """
(async()=>{
  await pairDevice({code:'12345678'});
  assert.equal(token,'issued');assert.equal(stored,'issued');assert.equal(shown,true);
  assert.equal(limit,10485760);
})().catch(error=>{console.error(error);process.exit(1);});
""")

def test_phone_shrinks_only_oversized_or_very_large_photos():
    source = (ROOT/'static/js/mobile.js').read_text()
    start = source.index('function needsShrink(')
    function = source[start:source.index('async function request(', start)]
    run("""
const assert=require('node:assert/strict');
const MAX_IMAGE_EDGE=3000, LIMIT=10*1024*1024;
""" + function + """
assert.equal(needsShrink(11*1024*1024,1200,900,LIMIT),true);
assert.equal(needsShrink(2*1024*1024,3001,900,LIMIT),true);
assert.equal(needsShrink(2*1024*1024,3000,3000,LIMIT),false);
assert.equal(needsShrink(2*1024*1024,1200,900,LIMIT),false);
""")

def test_phone_network_failure_explains_lan_prerequisites():
    source = (ROOT/'static/js/mobile.js').read_text()
    start = source.index('function friendlyError(')
    function = source[start:source.index('// Compress only', start)]
    run("""
const assert=require('node:assert/strict');
""" + function + """
assert.match(friendlyError(new TypeError('Failed to fetch')),/同一 Wi-Fi/);
assert.match(friendlyError(new TypeError('Failed to fetch')),/防火墙/);
assert.match(friendlyError(Object.assign(new Error('x'),{name:'AbortError'})),/超时/);
assert.equal(friendlyError(new Error('配对码错误')),'配对码错误');
""")

def test_phone_page_exposes_progress_retake_and_lan_help():
    html = (ROOT/'static/mobile.html').read_text()
    for marker in ('id="uploadProgress"', 'id="retake"', 'id="connectionHelp"', 'AP 隔离',
                   '/static/lib/katex/katex.min.css', '/static/lib/marked/marked.min.js',
                   '/static/lib/dompurify/purify.min.js', '/static/lib/katex/contrib/auto-render.min.js'):
        assert marker in html

def test_desktop_mobile_panel_offers_copy_and_lan_checks():
    html = (ROOT/'static/index.html').read_text()
    for marker in ('id="mobileLinkCopy"', 'AP 隔离'):
        assert marker in html

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


def test_phone_bank_markdown_transforms_choices_fillins_and_images():
    source = (ROOT/'static/js/mobile.js').read_text()
    start = source.index('function protectMobileLiterals')
    functions = source[start:source.index('// Compress only photos', start)]
    run("""
const assert=require('node:assert/strict');
global.window={};
""" + functions + """
const markdown=mobileQuestionMarkdown;
const choices=markdown('选择：\\\\begin{choices}\\\\item $x$\\\\item $y$\\\\end{choices}');
assert.ok(choices.includes('**A.** $x$')&&choices.includes('**B.** $y$'));
assert.ok(!choices.includes('\\\\begin{choices}'));
const fill=markdown('填空\\\\fillin 与 $x=\\\\fillin$');
assert.ok(fill.includes('class="mobile-fillin"'));
assert.ok(fill.includes('\\\\underline{\\\\hspace{1.5cm}}'));
const image=markdown('题干 ![图](/static/uploads/a.png) 结束');
assert.ok(image.includes('（配图请在电脑查看）')&&!image.includes('/static/uploads/'));
const code=markdown('示例 `\\\\fillin` 保持原样');
assert.ok(code.includes('`\\\\fillin`')&&!code.includes('mobile-fillin'));
const display=markdown('\\\\begin{align}a&=1\\\\\\\\b&=2\\\\end{align}');
assert.ok(display.startsWith('$$\\\\begin{aligned}')&&display.endsWith('\\\\end{aligned}$$'));
""")


def test_phone_bank_renders_markdown_then_math_and_falls_back_to_text():
    source = (ROOT/'static/js/mobile.js').read_text()
    start = source.index('function protectMobileLiterals')
    functions = source[start:source.index('// Compress only photos', start)]
    run("""
const assert=require('node:assert/strict');
let math=null, sanitized=null;
const target={innerHTML:'',textContent:'',replaceChildren(){this.innerHTML='';this.textContent='';}};
global.window={marked:{parse:value=>'<p>'+value+'</p>'},DOMPurify:{sanitize:value=>{sanitized=value;return value;}},
 renderMathInElement:(node,options)=>{math={node,options};}};
""" + functions + """
const renderQuestion=renderMobileQuestion;
renderQuestion(target,'题 $x+1$');
assert.ok(target.innerHTML.includes('题 $x+1$'));
assert.ok(sanitized.includes('题 $x+1$'));
assert.equal(math.node,target);
assert.ok(math.options.throwOnError===false);
assert.ok(math.options.macros['\\\\iu']==='\\\\mathrm{i}');
renderQuestion(target,'  ');
assert.equal(target.textContent,'暂无内容');
window.renderMathInElement=()=>{throw new Error('katex failed');};
renderQuestion(target,'原始题干');
assert.equal(target.textContent,'原始题干');
""")
