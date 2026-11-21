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
