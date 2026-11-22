"""Submission must cancel the native navigation before asynchronous requests."""
from pathlib import Path
import shutil
import subprocess


def test_section_navigation_preserves_forms_and_does_not_start_requests():
    source = (Path(__file__).resolve().parents[1] / 'static/js/learning.js').read_text()
    shipped = source[source.index('function showPanel('):source.index('function current(')]
    script = r'''
const assert=require('node:assert/strict');
const panels=['today','practice','scan','review','profile'].map(name=>({dataset:{learningPanel:name},hidden:name!=='today',answer:'未提交的作答',scan:{selected:true}}));
const buttons=panels.map(panel=>({dataset:{learningSection:panel.dataset.learningPanel},setAttribute(key,value){this[key]=value;}}));
const document={querySelector(selector){return panels.find(panel=>selector===`[data-learning-panel="${panel.dataset.learningPanel}"]`);},querySelectorAll(selector){return selector==='[data-learning-panel]'?panels:buttons;}};
''' + shipped + r'''
const original=panels.map(panel=>[panel.answer,panel.scan]);
for(const name of ['scan','practice','profile','review','today']) {
 showPanel(name);
 assert.deepEqual(panels.filter(panel=>!panel.hidden).map(panel=>panel.dataset.learningPanel),[name]);
 assert.deepEqual(buttons.filter(button=>button['aria-pressed']==='true').map(button=>button.dataset.learningSection),[name]);
 panels.forEach((panel,i)=>{assert.equal(panel.answer,original[i][0]);assert.equal(panel.scan,original[i][1]);});
}
showPanel('missing');assert.equal(panels[0].hidden,false);
'''
    result = subprocess.run([shutil.which('node'), '-'], input=script, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


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


def test_daily_plan_ignores_stale_scope_responses_and_starts_online_practice():
    source = (Path(__file__).resolve().parents[1] / 'static/js/learning.js').read_text()
    shipped = source[source.index('async function loadDaily('):source.index('async function loadRecommendations(')]
    script = r"""
const assert=require('node:assert/strict');
let learnerId=1,serial=1,dailySerial=0,dailyData=null,chosen=[],opened=[],pending=[];
const elements={};const $=id=>elements[id]||(elements[id]={value:'',children:[],replaceChildren(){this.children=[];},append(child){this.children.push(child);}});
const current=(owner,token)=>owner===learnerId&&token===serial;
const request=(url,method,body)=>new Promise((resolve,reject)=>pending.push({url,method,body,resolve,reject}));
const outcomes={ungraded:'待核对',correct:'正确'};
const action=fn=>fn;const chooseQuestion=(...args)=>chosen.push(args);const openAttempt=id=>opened.push(id);
function node(tag,text,host){const e={tag,textContent:text,children:[],append(child){this.children.push(child);},addEventListener(_,fn){this.click=fn;}};host?.append(e);return e;}
""" + shipped + r"""
(async()=>{
 $('learningEducationStage').value='高中';$('learningSubject').value='数学';
 const old=loadDaily();assert.equal($('learningDailyStart').hidden,true);
 $('learningSubject').value='物理';const newer=loadDaily(true);
 assert.equal(pending[1].method,'POST');assert.equal(pending[1].body.subject,'物理');
 const result={date:'2026-11-22',education_stage:'高中',subject:'物理',generated:true,total:2,completed:1,graded:0,correct:0,unavailable:0,note:'依据原记录',items:[
   {id:7,completed:false,available:true,content_excerpt:'物理题',reason:'到期复习',evidence_ids:['original']},
   {id:8,completed:true,available:true,outcome:'ungraded',attempt_id:'actual',evidence_ids:[]}
 ]};
 pending[1].resolve(result);await newer;
 assert.equal(dailyData,result);assert.equal($('learningDailyProgress').value,1);
 assert.ok($('learningDailyStatus').textContent.includes('物理'));assert.ok($('learningDailyStatus').textContent.includes('正确 0'));
 const flatten=e=>[e,...e.children.flatMap(flatten)];
 const buttons=flatten($('learningDailyList')).filter(x=>x.tag==='button');
 buttons.find(x=>x.textContent==='练习这道题').click();assert.deepEqual(chosen,[[7,true]]);
 buttons.find(x=>x.textContent==='查看作答并核对').click();assert.deepEqual(opened,['actual']);
 pending[0].resolve({...result,subject:'数学',items:[]});await old;
 assert.equal(dailyData,result,'old scope response must not replace the current daily plan');
 const changing=loadDaily();learnerId=2;serial++;pending[2].reject(new Error('old profile unavailable'));await changing;
 assert.ok(!$('learningDailyStatus').textContent.includes('old profile unavailable'));
})().catch(error=>{console.error(error);process.exit(1);});
"""
    result = subprocess.run([shutil.which('node'), '-'], input=script, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
