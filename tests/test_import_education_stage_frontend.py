"""Plain question imports carry the import-wide education stage into cards."""

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def stage_helper():
    source = (ROOT / 'static/js/import.js').read_text()
    start = source.index('function applyImportDefaultEducationStage(')
    end = source.index('function renderParsedQuestionsList(', start)
    return source[start:end]


def test_default_education_stage_fills_only_unlabelled_cards():
    node = shutil.which('node')
    assert node, 'Node is required for the import-stage regression'
    script = stage_helper() + r"""
const fresh = {content: '题目'};
applyImportDefaultEducationStage(fresh, '高中');
if (fresh.education_stage !== '高中') throw new Error('未标注题卡应带入学段');

const explicit = {education_stage: '初中'};
applyImportDefaultEducationStage(explicit, '高中');
if (explicit.education_stage !== '初中') throw new Error('已有学段不得被覆盖');

const untouched = {education_stage: '未标注'};
applyImportDefaultEducationStage(untouched, '未标注');
if (untouched.education_stage !== '未标注') throw new Error('未选择学段时不得改动');

console.log('ok');
"""
    result = subprocess.run([node, '-e', "eval(require('node:fs').readFileSync(0,'utf8'))"],
                            input=script, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ok'


def test_card_renderer_applies_the_import_stage():
    source = (ROOT / 'static/js/import.js').read_text()
    start = source.index('function renderParsedQuestionsList(')
    body = source[start:source.index('function renderParsedCardPreview(', start)]
    assert "getElementById('importEducationStage')" in body
    assert 'applyImportDefaultEducationStage(question, defaultEducationStage)' in body
