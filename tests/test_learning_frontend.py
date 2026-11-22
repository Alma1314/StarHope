"""Submission must cancel the native navigation before asynchronous requests."""
from pathlib import Path
import shutil
import subprocess


def test_submit_handler_runs_prevent_default_synchronously():
    source = (Path(__file__).resolve().parents[1] / 'static/js/learning.js').read_text()
    start = source.index('function action(handler)')
    shipped = source[start:source.index('async function mutate', start)]
    script = "const assert=require('node:assert/strict');let error='';const message=value=>{error=value;};\n" + shipped + """
    let prevented=false;
    const handler=action(async event=>{event.preventDefault();await Promise.resolve();});
    handler({preventDefault(){prevented=true;}});
    assert.equal(prevented,true,'native form submission would reload and lose the answer');
    assert.equal(error,'');
    """
    result = subprocess.run([shutil.which('node'), '-'], input=script, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_scan_upload_keeps_form_data_and_upload_wait_budget():
    source = (Path(__file__).resolve().parents[1] / 'static/js/learning.js').read_text()
    shipped = source[source.index('async function request('):source.index('function action(handler)')]
    script = """
const assert=require('node:assert/strict');
let observed, waitBudget;
global.window={StarHopeTimeouts:{UPLOAD_MS:600000,QUERY_MS:60000}};
global.setTimeout=(_,delay)=>{waitBudget=delay;return 1;};global.clearTimeout=()=>{};
global.fetch=async(url,options)=>{observed={url,options};return{ok:true,json:async()=>({task_id:'fixture'})};};
""" + shipped + """
(async()=>{
 const form=new FormData();form.append('request_id','fixture');
 await request('learners/1/scan-tasks','POST',form);
 assert.equal(observed.options.body,form);
 assert.equal(observed.options.headers,undefined,'browser must supply multipart boundary');
 assert.equal(waitBudget,600000);
 await request('learners/1/recommendations');assert.equal(waitBudget,60000);
})().catch(error=>{console.error(error);process.exit(1);});
"""
    result = subprocess.run([shutil.which('node'), '-'], input=script, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_late_scan_upload_cannot_poll_or_change_a_different_learner():
    source = (Path(__file__).resolve().parents[1] / 'static/js/learning.js').read_text()
    shipped = source[source.index('async function startScan('):source.index('async function pollScan(')]
    script = """
const assert=require('node:assert/strict');const crypto=require('node:crypto').webcrypto;
let learnerId=1,saving=false,scanData=null,scanSaved=false,scanTask='',scanPending=null,scanTimer=null,serial=7,polls=0,resolve;
const elements={learningScanFiles:{files:[new Blob(['synthetic'],{type:'image/png'})]},learningScanSubject:{value:'数学'},learningScanStage:{value:'高中'}};
const $=id=>elements[id]||(elements[id]={hidden:true,textContent:'',replaceChildren(){}});
const window={confirm:()=>true};const rememberScan=()=>{};const mutate=async(_,fn)=>fn();
const current=(owner,token)=>owner===learnerId&&token===serial;
const request=()=>new Promise(done=>{resolve=done;});const pollScan=async()=>{polls++;};
""" + shipped + """
(async()=>{
 let prevented=false;
 const work=startScan({preventDefault(){prevented=true;}});
 assert.equal(prevented,true,'form must not reload and discard the scan');
 assert.ok(scanTask);assert.equal(elements.learningScanReconnect.hidden,false);
 learnerId=2;serial++;resolve({task_id:scanTask});await work;
 assert.equal(polls,0,'late response must not query the new learner task path');
})().catch(error=>{console.error(error);process.exit(1);});
"""
    result = subprocess.run([shutil.which('node'), '-'], input=script, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_choice_controls_submit_selection_and_keep_retry_payload():
    source = (Path(__file__).resolve().parents[1] / 'static/js/learning.js').read_text()
    helpers = source[source.index('function selectedChoices('):source.index('function mayReplace(')]
    controls = source[source.index('function syncOrigin('):source.index('async function chooseQuestion(')]
    submit = source[source.index('async function submitAttempt('):source.index('function showReview(')]
    script = r"""
const assert=require('node:assert/strict');
class Element {
 constructor(){this.children=[];this.value='';this.textContent='';this.hidden=false;this.checked=false;}
 append(child){this.children.push(child);}
 replaceChildren(){this.children=[];}
 querySelectorAll(selector){
   const all=this.children.flatMap(child=>[child,...child.querySelectorAll('*')]);
   return selector==='*'?all:selector==='input:checked'?all.filter(x=>x.tag==='input'&&x.checked):selector==='input'?all.filter(x=>x.tag==='input'):this.labels||[];
 }
 querySelector(selector){return this.querySelectorAll(selector)[0];}
 focus(){this.focused=true;}
}
const elements={};const $=id=>elements[id]||(elements[id]=new Element());
function node(tag,text,host,className){const e=new Element();e.tag=tag;if(text!==undefined)e.textContent=text;host?.append(e);return e;}
let question={question_type:'single_choice',question_id:1,question_revision:'rev'},review=null;
let learnerId=1,saving=false,pendingPayload=null,requestId='same-uuid',started=0,status='',sent=[],fail=false;
const window={confirm:()=>true};const message=text=>{status=text;};
const mutate=async(_,fn)=>fn();const showReview=row=>{review=row;};const refresh=async()=>{};
const request=async(_,method,payload)=>{sent.push(payload);if(fail)throw new Error('connection lost');return payload;};
""" + helpers + controls + submit + r"""
(async()=>{
 $('learningOrigin').value='practice';renderChoiceAnswer();syncOrigin();
 let inputs=$('learningChoiceOptions').querySelectorAll('input');
 assert.deepEqual(inputs.map(x=>x.value),['A','B','C','D']);
 assert.ok(inputs.every(x=>x.type==='radio'&&x.name==='learning-choice'));
 assert.equal($('learningAnswer').required,false);
 await submitAttempt({preventDefault(){}});assert.equal(sent.length,0);assert.equal(inputs[0].focused,true);
 inputs[1].checked=true;assert.equal(dirtyPractice(),true);assert.equal(practiceAnswer(),'B');
 await submitAttempt({preventDefault(){}});assert.equal(sent[0].student_answer,'B');assert.equal(sent[0].outcome,'ungraded');
 question={question_type:'multi_choice',question_id:2,question_revision:'next'};review=null;
 $('learningQuestionBody').labels='ABCDEF'.split('').map(textContent=>({textContent:textContent+'.'}));
 renderChoiceAnswer();syncOrigin();inputs=$('learningChoiceOptions').querySelectorAll('input');
 assert.equal(inputs.length,6);assert.ok(inputs.every(x=>x.type==='checkbox'&&!x.checked));
 inputs[3].checked=true;inputs[0].checked=true;$('learningAnswer').value='分析步骤';
 assert.equal(practiceAnswer(),'A、D\n分析步骤');
 $('learningOrigin').value='manual';syncOrigin();assert.equal(practiceAnswer(),'A、D\n分析步骤');
 $('learningOrigin').value='practice';fail=true;
 await assert.rejects(submitAttempt({preventDefault(){}}));
 const original=sent.at(-1);inputs[1].checked=true;$('learningAnswer').value='修改后的文字';fail=false;
 await submitAttempt({preventDefault(){}});assert.equal(sent.at(-1),original);assert.equal(sent.at(-1).request_id,'same-uuid');
 question={question_type:'detailed_answer'};$('learningAnswer').value='非选择题解答';
 renderChoiceAnswer();syncOrigin();assert.equal($('learningChoiceAnswer').hidden,true);
 assert.equal($('learningChoiceOptions').querySelectorAll('input').length,0);
 assert.equal($('learningAnswer').required,true);assert.equal(practiceAnswer(),'非选择题解答');
})().catch(error=>{console.error(error);process.exit(1);});
"""
    result = subprocess.run([shutil.which('node'), '-'], input=script, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
