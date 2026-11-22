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
