/* Book-specific controls share the existing import cards and save pipeline. */
function syncBookImportOptions() {
    const book = document.getElementById('pdfDocumentKind')?.value === 'book';
    ['bookSubjectOption', 'bookEducationStageOption', 'bookImportHint'].forEach(id => { document.getElementById(id).hidden = !book; });
    const importStage = document.getElementById('importEducationStageOption');
    if (importStage) importStage.hidden = book;
    const generate = document.getElementById('importGenerateAnswers');
    if (generate) { generate.disabled = book && Boolean(window.currentPdfFile); if (generate.disabled) generate.checked = false; }
}

function renderBookImportQuestion(host, source, options = {}) {
    host.replaceChildren();
    const text = String(source || '');
    if (typeof window.renderQuestionPreviewContent === 'function') {
        try {
            window.renderQuestionPreviewContent(host, text, options);
            return;
        } catch (_) { /* fall back to plain text below */ }
    }
    host.textContent = text;
}

function renderParsedSubjectFields(card, question, index) {
    const host = document.createElement('div');
    host.className = 'grid grid-cols-1 sm:grid-cols-2 gap-2';
    const label = document.createElement('label');
    label.className = 'text-xs text-slate-600'; label.textContent = '学科';
    const select = document.createElement('select');
    select.className = 'card-subject glass-select w-full px-2 py-2 rounded-lg';
    ['数学', '物理', '化学', '生物', '语文', '英语', '历史', '地理', '政治', '信息技术', '其他'].forEach(value => {
        const option = document.createElement('option'); option.value = value; option.textContent = value; select.append(option);
    });
    select.value = question.subject || '数学'; label.append(select); host.append(label);
    const stageLabel = document.createElement('label'); stageLabel.className = 'text-xs text-slate-600'; stageLabel.textContent = '教育学段';
    const stage = document.createElement('select'); stage.className = 'card-education-stage glass-select w-full px-2 py-2 rounded-lg';
    stage.innerHTML = educationStageOptions(question.education_stage || '未标注'); stageLabel.append(stage); host.append(stageLabel);
    const topicsLabel = document.createElement('label');
    topicsLabel.className = 'text-xs text-slate-600'; topicsLabel.textContent = '知识点（每行一个，可修改 AI 建议）';
    const topics = document.createElement('textarea'); topics.rows = 2; topics.maxLength = 2000;
    topics.className = 'card-subject-topics glass-input w-full px-2 py-2 rounded-lg';
    topics.value = question.subject_topics || ''; topicsLabel.append(topics); host.append(topicsLabel);
    card.insertBefore(host, card.children[1]);
    const syncVisibility = () => {
        const math = parsedUsesMathCurriculum(question);
        [card.querySelector('.card-compulsory')?.parentElement, card.querySelector('.card-knowledge-points'),
            card.querySelector('.card-classification-review')].filter(Boolean).forEach(el => {
                el.hidden = !math; el.style.display = math ? '' : 'none';
            });
    };
    const update = () => {
        question.education_stage = stage.value; question.subject = select.value; question.subject_topics = topics.value;
        syncVisibility();
        updateParsedSavedState(index);
        window.StarHopeDocumentImportSession?.saveSoon();
    };
    stage.addEventListener('change', update); select.addEventListener('change', update); topics.addEventListener('input', update); syncVisibility();
    if (question.book_import) {
        const note = document.createElement('p'); note.className = 'text-xs text-slate-500';
        note.textContent = '书籍题目按原书章节和 PDF 页码记录来源；知识点与难度是待核对的 AI 建议。'; host.after(note);
    }
}

function renderBookImportReport(report) {
    document.getElementById('bookImportReport')?.remove();
    const book = report?.book_import;
    if (!book) return;
    const panel = document.createElement('details'); panel.id = 'bookImportReport'; panel.open = true;
    panel.className = 'glass-panel rounded-xl border border-indigo-200 p-3 space-y-2 text-xs shrink-0';
    const heading = document.createElement('summary'); heading.className = 'font-semibold cursor-pointer';
    heading.textContent = `书籍提取报告：${book.questions || 0} 题，已配对 ${book.matched_answers || 0} 份集中答案，${book.missing_answers || 0} 题暂无答案`;
    panel.append(heading);
    const note = document.createElement('p');
    note.textContent = '仅提取书中原答案，未自动解题。AI 可能漏题或识别有误，请对照原页核对；以下原答案可人工指定题卡。'; panel.append(note);
    (book.unresolved || []).forEach(value => { const row = document.createElement('p'); row.textContent = value; row.className = 'text-amber-800 whitespace-pre-wrap'; panel.append(row); });
    (book.unmatched_answers || []).forEach(answer => {
        const item = document.createElement('details'); item.className = 'border-t pt-2';
        const summary = document.createElement('summary'); summary.className = 'cursor-pointer';
        summary.textContent = ['待配对原答案', answer.chapter, answer.section, answer.number, `PDF第${answer.start_page}–${answer.end_page}页`].filter(Boolean).join(' · '); item.append(summary);
        const text = document.createElement('div'); text.className = 'book-import-answer-preview ui-rendered-question';
        item.addEventListener('toggle', () => renderBookImportQuestion(text, answer.content), { once: true });
        item.append(text);
        const label = document.createElement('label'); label.textContent = '人工指定题卡：';
        const select = document.createElement('select'); select.className = 'glass-select max-w-full px-2 py-2 rounded-lg';
        const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = '请选择并对照原书确认'; select.append(placeholder);
        parsedQuestionsData.forEach((question, index) => {
            const option = document.createElement('option'); option.value = String(index);
            const type = typeof getTypeText === 'function' ? getTypeText(question.question_type) : (question.question_type || '未标题型');
            const source = String(question.source || '').slice(0, 80);
            option.textContent = `第${index + 1}题 · ${type}${source ? ' · ' + source : ''}`; select.append(option);
        });
        label.append(select); item.append(label);
        const questionPreview = document.createElement('div');
        questionPreview.className = 'book-import-question-preview ui-rendered-question';
        questionPreview.hidden = true;
        item.append(questionPreview);
        select.addEventListener('change', () => {
            const question = select.value === '' ? null : parsedQuestionsData[Number(select.value)];
            if (!question) {
                questionPreview.hidden = true; questionPreview.replaceChildren(); return;
            }
            questionPreview.hidden = false;
            renderBookImportQuestion(questionPreview, question.content || '');
        });
        const button = document.createElement('button'); button.type = 'button'; button.className = 'glass-btn px-3 py-2 rounded-lg'; button.textContent = '确认配对并填入';
        button.addEventListener('click', () => {
            if (select.value === '') return;
            const index = Number(select.value), question = parsedQuestionsData[index];
            const card = document.getElementById(`parsed-card-${index}`), input = card?.querySelector('.card-answer-textarea');
            if (!question || !input || question.saved || input.value.trim()) { showToast('请选一张尚未入库且答案为空的题卡，已有答案可在题卡中人工编辑。', 'warning'); return; }
            input.value = answer.content; input.dispatchEvent(new Event('input', {bubbles: true}));
            const source = card.querySelector('.card-source'); source.value += ` · 原答案PDF第${answer.start_page}–${answer.end_page}页`;
            source.dispatchEvent(new Event('change', {bubbles: true}));
            question.answer_markdown = answer.content; question.source = source.value;
            button.disabled = true; button.textContent = `已填入第${index + 1}题`;
            window.StarHopeDocumentImportSession?.saveSoon();
        }); item.append(button); panel.append(item);
    });
    document.getElementById('parsedQuestionsWrapper')?.prepend(panel);
}
