"""Saved-paper structure report is computed locally without grading data."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_paper_report_aggregates_saved_question_structure():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the paper report regression")

    source = (ROOT / "static/js/paper.js").read_text(encoding="utf-8")
    start = source.index("    function composePaperReport(questions)")
    end = source.index("    window.composePaperReport = composePaperReport;", start)
    script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {};
vm.createContext(context);
vm.runInContext(SRC + '\n;globalThis.composePaperReport = composePaperReport;', context);
const local = value => JSON.parse(JSON.stringify(value));
const report = local(context.composePaperReport([
  {score: 5, question: {question_type: 'single_choice', difficulty: 'normal',
    knowledge_points: [{name: '函数', role: 'main'}],
    category_compulsory: '必修一', category_chapter: '函数'}},
  {score: 10, question: {question_type: 'detailed_answer', difficulty: 'challenge',
    knowledge_points: [{name: '函数', role: 'auxiliary'}, {name: '导数', role: 'main'}],
    category_compulsory: '必修一', category_chapter: '函数'}},
  {score: 0, question: {question_type: 'single_choice', difficulty: 'normal',
    knowledge_points: [], category_compulsory: '', category_chapter: ''}}
]));
assert.equal(report.count, 3);
assert.equal(report.totalScore, 15);
assert.deepEqual(report.types, [{key: 'single_choice', count: 2}, {key: 'detailed_answer', count: 1}]);
assert.deepEqual(report.difficulties, [{key: 'normal', count: 2}, {key: 'challenge', count: 1}]);
assert.deepEqual(report.points.map(point => [point.name, point.count, point.main]),
  [['函数', 2, 1], ['导数', 1, 1]]);
assert.deepEqual(report.chapters, [{key: '必修一 · 函数', count: 2}, {key: '未标注', count: 1}]);
assert.deepEqual(local(context.composePaperReport(null)), {count: 0, totalScore: 0, types: [], difficulties: [], points: [], chapters: []});
"""
    script = script.replace("SRC", json.dumps(source[start:end]))
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)


def test_saved_paper_cards_expose_report_entry():
    paper_source = (ROOT / "static/js/paper.js").read_text(encoding="utf-8")
    css_source = (ROOT / "static/css/app.css").read_text(encoding="utf-8")

    assert 'onclick="openPaperReport(${paperId})"' in paper_source
    assert "window.openPaperReport = openPaperReport" in paper_source
    assert "window.composePaperReport = composePaperReport" in paper_source
    assert ".saved-paper-report-action" in css_source
    assert ".paper-report-overlay" in css_source
    assert "不含作答与评分" in paper_source
