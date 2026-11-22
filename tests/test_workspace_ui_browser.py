"""Shared UI checks on the disposable app, with no real data or model calls."""
import json

import pytest

from test_preview_browser import browser, pytestmark  # noqa: F401


@pytest.mark.parametrize('width', [1440, 768, 375])
@pytest.mark.parametrize('dark', [False, True])
def test_workspaces_and_settings_fit_the_viewport(browser, tmp_path, width, dark):
    browser.command('set', 'viewport', str(width), '812')
    browser.evaluate(f"document.documentElement.classList.toggle('dark', {json.dumps(dark)});true")
    for workspace in ['dashboard', 'bank', 'import', 'paper', 'records', 'learning', 'qa']:
        browser.evaluate("openSavedPapersModal();true" if workspace == 'records'
                         else f"selectWorkspace('{workspace}', '{workspace}');true")
        browser.settle()
        layout = browser.evaluate("""(() => {
            const workspace = document.getElementById('%sWorkspaceSection');
            return {width:innerWidth, document:document.documentElement.scrollWidth,
                client:workspace.clientWidth, scroll:workspace.scrollWidth,
                height:workspace.getBoundingClientRect().height,
                targets:[...document.querySelectorAll('#appNavPrimary > button')]
                    .map(button=>button.getBoundingClientRect())
                    .filter(box=>box.width).map(box=>[box.width, box.height])};
        })()""" % workspace)
        assert layout['document'] <= layout['width'] + 1, (workspace, layout)
        assert layout['scroll'] <= layout['client'] + 1, (workspace, layout)
        assert layout['height'] > 200, (workspace, layout)
        assert all(w >= 44 and h >= 44 for w, h in layout['targets'])

    browser.evaluate('openSettingsModal();true')
    browser.command('wait', '--fn', "document.getElementById('settingsModal').getAttribute('aria-hidden')==='false'")
    browser.settle()
    layout = browser.evaluate("""(() => {
        const modal=document.querySelector('#settingsModal > .glass-modal');
        const r=modal.getBoundingClientRect();
        return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:innerWidth,height:innerHeight,
            scroll:modal.scrollWidth,client:modal.clientWidth,focus:document.activeElement.id};
    })()""")
    assert layout['left'] >= 0 and layout['right'] <= layout['width'] + 1
    assert layout['top'] >= 0 and layout['bottom'] <= layout['height'] + 1
    assert layout['scroll'] <= layout['client'] + 1
    assert layout['focus'] == 'settingsModalTitle'
    browser.command('screenshot', str(tmp_path / f'settings-{width}-{dark}.png'))
    browser.evaluate('closeSettingsModal();true')
    browser.settle()


def test_learning_sections_keep_unsaved_work_and_daily_practice_is_reachable(browser, tmp_path):
    browser.command('set', 'viewport', '375', '812')
    browser.evaluate(r"""(async()=>{
        const form=new FormData();
        Object.entries({content:String.raw`UI 回归 ${crypto.randomUUID()}：求 $x^2$ 的值。\begin{choices}\item $1$\item $2$\item $3$\item $4$\end{choices}`,
            question_type:'single_choice',subject:'数学',education_stage:'高中',difficulty:'normal'})
            .forEach(([key,value])=>form.set(key,value));
        const response=await fetch('/api/questions',{method:'POST',body:form});
        if(!response.ok)throw Error(await response.text());
        selectWorkspace('learning','学习档案');
    })()""")
    browser.evaluate("""(() => {
        document.getElementById('learnerCreate').click();
        const form=document.querySelector('#learnerDialog form');
        form.querySelector('input:not([type=checkbox])').value='UI 合成档案';
        form.querySelector('input[type=checkbox]').checked=false;
        form.requestSubmit();return true;
    })()""")
    browser.command('wait', '--fn', "!document.getElementById('learnerDialog').open && !document.getElementById('learningContent').hidden")
    browser.command('wait', '--fn', "!document.getElementById('learningDailyGenerate').hidden")
    browser.evaluate("document.getElementById('learningDailyGenerate').click();true")
    browser.command('wait', '--fn', "!document.getElementById('learningDailyStart').hidden")
    browser.evaluate("document.getElementById('learningDailyStart').click();true")
    browser.command('wait', '--fn', "!document.getElementById('learningAttemptForm').hidden")
    browser.settle()
    assert browser.evaluate("!document.getElementById('learning-panel-practice').hidden")
    assert browser.evaluate("document.activeElement.id") == 'learningQuestionMeta'
    browser.evaluate("document.querySelector('#learningChoiceOptions input').checked=true;document.getElementById('learningAnswer').value='保留解题步骤';true")
    for section in ['scan', 'profile', 'review', 'today', 'practice']:
        browser.evaluate(f"document.querySelector('[data-learning-section={section}]').click();true")
        browser.settle()
        if section == 'scan':
            browser.evaluate("""(() => {
                const files=new DataTransfer();files.items.add(new File(['synthetic'],'ui-fixture.png',{type:'image/png'}));
                document.getElementById('learningScanFiles').files=files.files;return true;
            })()""")
        assert browser.evaluate("document.getElementById('learningAnswer').value") == '保留解题步骤'
        assert browser.evaluate("document.querySelector('#learningChoiceOptions input').checked")
        assert browser.evaluate("document.getElementById('learningScanFiles').files[0].name") == 'ui-fixture.png'
        assert browser.evaluate("document.querySelectorAll('[data-learning-panel]:not([hidden])').length") == 1
    assert browser.evaluate("document.querySelector('#learningDailyProgress').getAttribute('value')") == '0'
    browser.command('screenshot', str(tmp_path / 'learning-preserved-mobile.png'))


def test_primary_actions_have_readable_contrast_in_all_themes(browser):
    result = browser.evaluate("""(async()=>{
        const luminance=color=>{
            const rgb=color.match(/[\\d.]+/g).slice(0,3).map(Number).map(v=>v/255)
                .map(v=>v<=.04045?v/12.92:Math.pow((v+.055)/1.055,2.4));
            return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
        };
        const original=document.documentElement.className;
        const probe=document.createElement('button');probe.className='glass-btn ui-primary-action';
        probe.style.transition='none';document.body.append(probe);
        const results=[];
        try {
            for(const dark of [false,true])for(const theme of ['obsidian','violet','ocean','emerald','amber','crimson']){
                document.documentElement.className='theme-'+theme+(dark?' dark':'');
                const style=getComputedStyle(probe);
                const bg=luminance(style.backgroundColor),fg=luminance(style.color);
                results.push({theme,dark,ratio:(Math.max(bg,fg)+.05)/(Math.min(bg,fg)+.05)});
            }
        } finally { probe.remove();document.documentElement.className=original; }
        return results;
    })()""")
    assert all(row['ratio'] >= 4.5 for row in result), result
