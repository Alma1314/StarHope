"""Six-dimension learning profile is derived locally from existing records."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_learning_ability_profile_uses_only_recorded_evidence():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the learning profile regression")

    source = (ROOT / "static/js/learning.js").read_text(encoding="utf-8")
    start = source.index("    function clampPercent(value)")
    end = source.index("    function renderResources()", start)
    script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {Math};
vm.createContext(context);
vm.runInContext(SRC + '\n;globalThis.computeAbility = computeAbility;', context);
const {computeAbility} = context;
const local = value => JSON.parse(JSON.stringify(value));

const rows = local(computeAbility({
  attempts: 10, graded: 8, correct: 4, partial: 2, incorrect: 2,
  mastered: 2, open_mistakes: 1,
  mastery: [
    {status: '较稳定', sample_count: 4},
    {status: '较稳定', sample_count: 5},
    {status: '练习中', sample_count: 6},
    {status: '较稳定', sample_count: 2}
  ]
}));
assert.deepEqual(rows.map(row => row.key), ['graded', 'accuracy', 'stability', 'recovery', 'breadth', 'volume']);
assert.deepEqual(rows.map(row => row.value), [80, 63, 67, 67, 40, 27]);
assert.match(rows[2].detail, /2 \/ 3/);

const empty = local(computeAbility({attempts: 0, graded: 0, mastery: []}));
assert.deepEqual(empty.map(row => row.value), [0, 0, 0, 0, 0, 0]);
assert.equal(empty[0].detail, '还没有已核对记录');

const noMistakes = local(computeAbility({attempts: 3, graded: 3, correct: 3, partial: 0, incorrect: 0, mastered: 0, open_mistakes: 0, mastery: []}));
assert.equal(noMistakes[3].value, 0);
assert.equal(noMistakes[3].detail, '暂无错题记录');
"""
    script = script.replace("SRC", json.dumps(source[start:end]))
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)


def test_learning_panel_exposes_radar_and_resource_index():
    index_source = (ROOT / "static/index.html").read_text(encoding="utf-8")
    learning_source = (ROOT / "static/js/learning.js").read_text(encoding="utf-8")
    css_source = (ROOT / "static/css/app.css").read_text(encoding="utf-8")

    for element_id in ("learningAbility", "learningResources"):
        assert f'id="{element_id}"' in index_source
    assert "renderAbility(data)" in learning_source
    assert "window.LearningWorkspace" in learning_source
    assert "return {computeAbility, receivePhoto" in learning_source
    assert "learning-ability-chart" in css_source
    assert "learning-resource-list" in css_source
    assert "target = '_blank'" in learning_source
    assert "noopener noreferrer" in learning_source
