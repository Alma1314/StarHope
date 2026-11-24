"""Bulk import cards defer Markdown/KaTeX previews until they are on screen."""

from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
IMPORT_JS = ROOT / 'static/js/import.js'


def preview_helpers():
    source = IMPORT_JS.read_text()
    start = source.index('function resetParsedCardPreviewRendering(')
    end = source.index('function renderParsedQuestionsList(', start)
    return 'let parsedCardPreviewObserver = null;\n' + source[start:end]


def test_card_previews_render_only_when_requested_and_visible():
    node = shutil.which('node')
    assert node, 'Node is required for the import preview regression'
    script = preview_helpers() + r"""
const render = card => { card.renders = (card.renders || 0) + 1; };
const hidden = {__parsedPreviewState: {visible: false, pending: false, rendered: false}, __renderParsedPreview: null};
hidden.__renderParsedPreview = () => render(hidden);
requestParsedCardPreview(hidden);
if (hidden.renders) throw new Error('离屏题卡不得立即渲染');
if (!hidden.__parsedPreviewState.pending) throw new Error('离屏编辑应记为待渲染');

hidden.__parsedPreviewState.visible = true;
renderParsedCardPreviewNow(hidden);
if (hidden.renders !== 1) throw new Error('进入视口后应渲染一次');
if (hidden.__parsedPreviewState.pending) throw new Error('渲染后不应再待渲染');

requestParsedCardPreview(hidden);
if (hidden.renders !== 2) throw new Error('可见题卡编辑后应立即重渲染');

const stale = {__parsedPreviewState: {visible: false, pending: true, rendered: true}, renders: 0};
stale.__renderParsedPreview = () => render(stale);
renderParsedCardPreviewNow(stale);
if (stale.renders !== 1 || stale.__parsedPreviewState.pending) throw new Error('恢复待渲染题卡应清理状态');

const legacy = {renders: 0};
legacy.__renderParsedPreview = () => render(legacy);
requestParsedCardPreview(legacy);
if (legacy.renders !== 1) throw new Error('无观察器时应保持原即时渲染行为');

console.log('ok');
"""
    result = subprocess.run([node, '-e', "eval(require('node:fs').readFileSync(0,'utf8'))"],
                            input=script, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ok'


def test_import_list_observes_cards_instead_of_rendering_all_previews():
    source = IMPORT_JS.read_text()
    start = source.index('function renderParsedQuestionsList(')
    body = source[start:source.index('function setupCardCategoryLinkage(', start)]
    assert 'resetParsedCardPreviewRendering();' in body
    assert 'previewObserver.observe(card)' in body
    assert 'card.__renderParsedPreview = triggerPreview;' in body
    # The loop must not eagerly render every card preview.
    assert 'requestParsedCardPreview(card), 200' in body
    assert '\n                triggerPreview();' not in body
