/* Multi-subject editing and explicit review of bounded AI suggestions. */
const EDUCATION_STAGES = ['未标注', '小学', '初中', '高中', '本科', '研究生', '其他'];
function educationStageOptions(current = '未标注', all = false) {
    return (all ? '<option value="">全部学段（跨学段）</option>' : '') + EDUCATION_STAGES.map(value =>
        `<option value="${value}" ${value === current ? 'selected' : ''}>${value}</option>`).join('');
}
document.addEventListener('DOMContentLoaded', () => {
    ['editEducationStage', 'filterEducationStage', 'learningEducationStage', 'bookEducationStage', 'importEducationStage'].forEach(id => {
        const select = document.getElementById(id);
        if (select) select.innerHTML = educationStageOptions(id.startsWith('edit') || id.startsWith('book') || id === 'importEducationStage' ? '未标注' : '', id.startsWith('filter') || id.startsWith('learning'));
    });
});
function readSubjectEditor() {
    return {education_stage: document.getElementById('editEducationStage')?.value || '未标注', subject: document.getElementById('editSubject')?.value || '数学',
        subject_topics: document.getElementById('editSubjectTopics')?.value || ''};
}
function hydrateSubjectEditor(record) {
    document.getElementById('editEducationStage').value = record.education_stage || '未标注';
    document.getElementById('editSubject').value = record.subject || '数学';
    document.getElementById('editSubjectTopics').value = record.subject_topics || '';
    syncSubjectEditor();
}
function syncSubjectEditor() {
    const math = readSubjectEditor().subject === '数学';
    const grid = document.getElementById('editCompulsory')?.parentElement.parentElement;
    if (grid) { grid.hidden = !math; grid.style.display = math ? '' : 'none'; }
    const picker = document.getElementById('editKnowledgePointPicker');
    if (picker) { picker.hidden = !math; picker.style.display = math ? '' : 'none'; }
    ['editCompulsory', 'editChapter'].forEach(id => document.getElementById(id)?.setAttribute('aria-required', String(math)));
    if (typeof refreshEditorFeedback === 'function') refreshEditorFeedback();
}

const SubjectAnnotations = (() => {
    let dialog, rows = [], options, taskId = '', timer, busy = false, returnFocus;
    const storageKey = 'starhope.subject-annotation-task';
    function node(tag, value, parent) {
        const el = document.createElement(tag);
        if (value !== undefined) el.textContent = value;
        if (parent) parent.append(el);
        return el;
    }
    async function request(path, body) {
        const response = await fetch('/api/subject-annotations/' + path, body === undefined ? {} : {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const result = await response.json();
        if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '标注请求失败');
        return result;
    }
    function message(value) { dialog.querySelector('[data-status]').textContent = value; }
    function setBusy(value) {
        busy = value;
        dialog.querySelectorAll('[data-fields] input, [data-fields] select, [data-fields] textarea, [data-action]').forEach(el => { el.disabled = value; el.setAttribute('aria-busy', String(value)); });
        dialog.querySelector('[data-cancel]').hidden = !value;
    }
    function selected() { return rows.filter(row => row.check.checked); }
    function draw(items) {
        const fields = dialog.querySelector('[data-fields]'); fields.replaceChildren();
        rows = items.map(item => {
            const card = node('section', undefined, fields);
            card.className = 'glass-panel p-3 rounded-xl space-y-2';
            const heading = node('label', undefined, card); heading.style.cssText = 'display:flex;align-items:center;gap:8px;min-height:44px;';
            const check = node('input', undefined, heading); check.type = 'checkbox'; check.checked = true;
            heading.append(document.createTextNode(' 题目 #' + item.id));
            const detail = node('details', undefined, card); node('summary', '查看题目（图片未提供给本次 AI）', detail);
            const content = node('div', undefined, detail); content.className = 'subject-annotation-preview';
            const fillPreview = () => {
                content.replaceChildren();
                const source = String(item.content || '');
                if (typeof window.renderQuestionPreviewContent === 'function') {
                    try {
                        window.renderQuestionPreviewContent(content, source, { includeImages: false });
                        return;
                    } catch (_) { /* fall back to plain text below */ }
                }
                content.textContent = source;
            };
            detail.addEventListener('toggle', fillPreview, { once: true });
            function select(labelText, choices, current) {
                const label = node('label', labelText, card); label.style.display = 'block';
                const input = node('select', undefined, label); input.className = 'glass-select w-full px-2 py-2 rounded-xl'; input.style.minHeight = '44px';
                Object.entries(choices).forEach(([value, text]) => { const opt = node('option', text, input); opt.value = value; });
                if (!Array.from(input.options).some(opt => opt.value === current)) { const opt = node('option', current || '未定', input); opt.value = current || ''; }
                input.value = current || ''; return input;
            }
            const stage = select('教育学段', Object.fromEntries(options.education_stages.map(s => [s, s])), item.education_stage || '未标注');
            const subject = select('学科', Object.fromEntries(options.subjects.map(s => [s, s])), item.subject);
            const difficulty = select('难度', options.difficulties, item.difficulty);
            const label = node('label', '学科知识点（每行一个）', card);
            const topics = node('textarea', undefined, label); topics.className = 'glass-input w-full px-2 py-2 rounded-xl'; topics.rows = 3; topics.maxLength = 2000; topics.value = item.subject_topics || '';
            if (item.reason) node('p', 'AI 建议依据（待核对）：' + item.reason, card);
            return {item, check, stage, subject, difficulty, topics};
        });
    }
    function ensureDialog() {
        if (dialog) return;
        dialog = document.createElement('dialog'); dialog.className = 'glass-panel'; dialog.setAttribute('aria-labelledby', 'subjectAnnotationHeading');
        dialog.style.cssText = 'width:min(900px,95vw);max-height:90vh;border-radius:16px;padding:20px;overflow:auto;';
        const title = node('h2', '学段、学科与批量标注', dialog); title.id = 'subjectAnnotationHeading'; title.tabIndex = -1; title.className = 'text-lg font-semibold';
        node('p', '每批最多20题。可手动编辑，或先生成 AI 建议；确认保存只应用勾选题。数学主辅知识点保留在原编辑器，本入口补充学科知识点名称。', dialog);
        const status = node('p', '', dialog); status.dataset.status = ''; status.setAttribute('role', 'status');
        const toolbar = node('div', undefined, dialog); toolbar.style.cssText = 'display:flex;flex-wrap:wrap;gap:12px;margin:12px 0;';
        function button(text, handler, action) {
            const btn = node('button', text, toolbar); btn.type = 'button'; btn.className = 'glass-btn p-2 rounded-xl text-xs'; btn.style.minHeight = '44px';
            if (action) btn.dataset.action = '';
            btn.onclick = async () => { try { await handler(); } catch (error) { message(error.message); } }; return btn;
        }
        button('生成选中题目的 AI 建议', async () => {
            const ids = selected().map(row => row.item.id);
            if (!ids.length) throw new Error('请先勾选题目');
            taskId = crypto.randomUUID(); sessionStorage.setItem(storageKey, taskId);
            setBusy(true); message('正在提交；生成建议可能需要几分钟。');
            try { await request('suggest', {ids, task_id: taskId}); await poll(); }
            catch (error) { setBusy(false); message(error.message + '。若提交结果未知，使用“接回任务”，不要重复生成。'); }
        }, true);
        button('接回任务', async () => {
            taskId = sessionStorage.getItem(storageKey) || taskId;
            if (!taskId) throw new Error('没有可接回的任务');
            await poll();
        }, true);
        const cancel = button('取消生成', async () => { await request('tasks/' + taskId + '/cancel', {}); await poll(); }); cancel.dataset.cancel = ''; cancel.hidden = true;
        button('确认保存选中标注', async () => {
            const items = selected().map(row => ({id: row.item.id, revision: row.item.revision,
                education_stage: row.stage.value, subject: row.subject.value, subject_topics: row.topics.value, difficulty: row.difficulty.value}));
            if (!items.length) throw new Error('请先勾选题目');
            setBusy(true);
            try {
                const result = await request('apply', {items});
                const fresh = await request('preview', {ids: rows.map(row => row.item.id)});
                draw(fresh.items); message('已保存 ' + result.count + ' 道题的标注。');
                const activeId = typeof EditorState !== 'undefined' ? EditorState.questionId : null;
                if (activeId && items.some(item => item.id === activeId)) {
                    if (typeof isEditorModified === 'function' && isEditorModified()) {
                        message('标注已保存；当前编辑器有未保存修改，请重新载入题目后再编辑分类。');
                    } else if (typeof selectQuestion === 'function') selectQuestion({id: activeId}, {silent: true});
                }
                if (typeof loadQuestions === 'function') loadQuestions();
            } finally { setBusy(false); }
        }, true);
        button('关闭', () => dialog.close());
        const fields = node('div', undefined, dialog); fields.dataset.fields = ''; fields.className = 'space-y-3';
        dialog.addEventListener('close', () => { clearTimeout(timer); returnFocus?.focus(); });
        document.body.append(dialog);
    }
    async function poll() {
        clearTimeout(timer);
        try {
            const state = await request('tasks/' + taskId);
            if (state.status === 'completed') {
                draw(state.items); setBusy(false);
                message('AI 建议已生成，尚未保存。请逐题核对，修改或取消勾选后确认保存。');
            } else if (state.status === 'error' || state.status === 'cancelled') {
                setBusy(false); message(state.error || '已取消，题库未修改。');
            } else {
                setBusy(true); message('正在生成 AI 建议，尚未修改题库。关闭窗口后可接回任务。');
                if (dialog.open) timer = setTimeout(poll, 2000);
            }
        } catch (error) { setBusy(false); message(error.message + '；可用“接回任务”再次查询。'); }
    }
    async function open() {
        ensureDialog(); returnFocus = document.activeElement;
        if (!dialog.open) { dialog.showModal(); dialog.querySelector('h2').focus(); }
        if (busy) { await poll(); return; }
        try {
            options = await request('options');
            const ids = Array.from(document.querySelectorAll('.bank-question-card[data-id]')).map(el => Number(el.dataset.id)).slice(0, 20);
            if (!ids.length) { message('当前页没有已入库题目，请先在题库中选择筛选范围。'); return; }
            draw((await request('preview', {ids})).items);
            message('已载入本页题目。生成建议会使用已配置的分类模型。');
        } catch (error) { message(error.message); }
    }
    return {open};
})();
function openSubjectAnnotations() { return SubjectAnnotations.open(); }
