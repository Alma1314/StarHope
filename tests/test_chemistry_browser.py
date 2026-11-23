"""Real offline preview, local chemfig compilation and source-preserving insert."""
import json
from test_preview_browser import browser, pytestmark


def test_chemistry_formula_and_structure_editor_flow(browser, tmp_path):
    browser.evaluate("selectWorkspace('bank', '题库管理');true")
    browser.command('click', '.bank-create-btn')
    browser.command('wait','--fn',"!document.getElementById('editorSection').classList.contains('hidden')")
    source=r'反应 $\ce{N2 + 3H2 <=>[\text{催化剂}] 2NH3}$，乙醇 \ce{CH3-CH2-OH}。'
    browser.evaluate("document.getElementById('editContent').value="+json.dumps(source)+";updateContentPreview();true")
    assert browser.evaluate("document.querySelectorAll('#contentPreview .katex').length")==2, browser.evaluate("document.getElementById('contentPreview').innerHTML")
    assert browser.evaluate("document.querySelectorAll('#contentPreview .katex-error').length")==0
    assert browser.evaluate("document.querySelector('#contentPreview annotation').textContent")==r'\ce{N2 + 3H2 <=>[\text{催化剂}] 2NH3}'
    browser.evaluate("openChemicalStructureWorkbench('content');true")
    assert browser.evaluate("document.getElementById('answerTikzWorkbenchCode').value")==r'\chemfig{*6(=-=-=-)}'
    browser.evaluate('compileTikzWorkbench();true')
    browser.command('wait','--fn',"!document.getElementById('insertAnswerTikzWorkbenchBtn').disabled")
    browser.command('wait','--fn',"document.getElementById('answerTikzWorkbenchPreviewImage').naturalWidth>0")
    browser.command('screenshot',str(tmp_path/'benzene-workbench.png'))
    code=browser.evaluate("document.getElementById('answerTikzWorkbenchCode').value")
    assert code.startswith(r'\begin{tikzpicture}') and r'\chemfig{*6(=-=-=-)}' in code
    browser.evaluate('applyTikzWorkbench();true')
    browser.command('wait','--fn',"document.querySelector('#contentPreview img')&&document.querySelector('#contentPreview img').naturalWidth>0")
    assert browser.evaluate("document.getElementById('editContent').value.startsWith("+json.dumps(source)+")")
    browser.command('screenshot',str(tmp_path/'chemical-question-preview.png'))
    # Check the physical PNG through the isolated app's private uploads, not production.
    from PIL import Image
    image_path=browser.evaluate("document.querySelector('#contentPreview img').getAttribute('src')")
    with Image.open(browser.root/image_path.lstrip('/').split('?')[0]) as image:
        assert image.width > 80 and image.height > 50
        image.save(tmp_path/'benzene.png')

    browser.command('set','viewport','393','851')
    assert browser.evaluate('document.documentElement.scrollWidth<=innerWidth')
    browser.command('screenshot',str(tmp_path/'chemical-question-narrow.png'))
