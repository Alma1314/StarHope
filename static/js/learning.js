/* Local profiles are learning records, not login or permission boundaries. */
window.LearningWorkspace = (() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const outcomes = {ungraded: '待核对', correct: '正确', partial: '部分正确', incorrect: '错误'};
    const errorTypes = {concept: '概念理解', condition: '条件理解', method: '解题方法', calculation: '计算运算', expression: '表达规范', omission: '步骤遗漏', unknown: '原因待核对'};
    let initialized = false, learnerId = 0, detail = null, question = null, review = null;
    let serial = 0, querySerial = 0, questionPage = 1, historyPage = 1, masteryLimit = 100;
    let started = 0, saving = false, requestId = '', pendingPayload = null, profileTask = '', profileTimer = null;
    let candidate = null, dialog = null;
    let automaticTimer = null;
    let reviewPlan = null, mistakePlan = null, explanation = null;
    let scanTask = '', scanData = null, scanSaved = false, scanTimer = null, scanPending = null, recommendationSerial = 0;
    let dailyData = null, dailySerial = 0;
    function node(tag, text, host, className) {
        const element = document.createElement(tag);
        if (text !== undefined) element.textContent = text;
        if (className) element.className = className;
        if (host) host.append(element);
        return element;
    }
    function message(value) { $('learningStatus').textContent = value; }
    function showPanel(name) {
        const target = document.querySelector(`[data-learning-panel="${name}"]`);
        if (!target) return;
        document.querySelectorAll('[data-learning-panel]').forEach(panel => { panel.hidden = panel !== target; });
        document.querySelectorAll('[data-learning-section]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.learningSection === name));
        });
    }
    function current(id, token) { return id === learnerId && token === serial; }
    async function request(path, method = 'GET', body) {
        const controller = new AbortController();
        const multipart = body instanceof FormData;
        const timer = setTimeout(() => controller.abort(), multipart ? (window.StarHopeTimeouts?.UPLOAD_MS || 600000) : (window.StarHopeTimeouts?.QUERY_MS || 60000));
        try {
            const response = await fetch('/api/learning/' + path, {method, signal: controller.signal,
                ...(body === undefined ? {} : multipart ? {body} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
            const result = await response.json();
            if (!response.ok) { const error = new Error(typeof result.detail === 'string' ? result.detail : '学习档案请求未完成'); error.status = response.status; throw error; }
            return result;
        } catch (error) {
            if (error.name === 'AbortError') throw new Error('本地请求超时，请重新查询原记录；不要重复提交新的作答。');
            throw error;
        } finally { clearTimeout(timer); }
    }
    function action(handler) {
        return event => {
            try { Promise.resolve(handler(event)).catch(error => message(error.message || '操作未完成')); }
            catch (error) { message(error.message || '操作未完成'); }
        };
    }
    async function mutate(button, handler) {
        if (saving) return;
        saving = true;
        const controls = [...$('learningWorkspaceSection').querySelectorAll('input,textarea,select,button')];
        const states = controls.map(control => [control, control.disabled]);
        controls.forEach(control => { control.disabled = true; });
        button?.setAttribute('aria-busy', 'true');
        try { return await handler(); }
        finally { saving = false; states.forEach(([control, disabled]) => { control.disabled = disabled; }); button?.removeAttribute('aria-busy'); }
    }
    function selectedChoices() { return [...$('learningChoiceOptions').querySelectorAll('input:checked')].map(input => input.value); }
    function isChoiceQuestion() { return ['single_choice', 'multi_choice'].includes(question?.question_type); }
    function practiceAnswer() {
        const text = $('learningAnswer').value.trim();
        return isChoiceQuestion() ? [selectedChoices().join('、'), text].filter(Boolean).join('\n') : $('learningAnswer').value;
    }
    function dirtyPractice() { return Boolean(question && practiceAnswer().trim() && !review); }
    function mayReplace() { return (!dirtyPractice() && !(scanData && !scanSaved)) || window.confirm('当前作答或扫描结果尚未录入。确定放弃未提交的编辑并切换？'); }
    function resetPractice() {
        question = null; review = null; pendingPayload = null; requestId = '';
        explanation = null; $('learningExplanation').hidden = true;
        $('learningAttemptForm').hidden = true; $('learningReview').hidden = true;
        $('learningChoiceOptions').replaceChildren(); $('learningChoiceAnswer').hidden = true;
    }
    function storageKey() { return 'starhope.learning.profile-task.' + learnerId; }
    function rememberTask(value) { try { value ? sessionStorage.setItem(storageKey(), value) : sessionStorage.removeItem(storageKey()); } catch (_) {} }
    function recalledTask() { try { return sessionStorage.getItem(storageKey()) || ''; } catch (_) { return ''; } }
    async function loadProfiles(preferred = learnerId) {
        const token = serial;
        const result = await request('learners');
        if (token !== serial) return;
        const select = $('learnerSelect'); select.replaceChildren();
        result.items.forEach(row => { const option = node('option', row.name, select); option.value = String(row.id); });
        learnerId = result.items.some(row => row.id === preferred) ? preferred : (result.items[0]?.id || 0);
        select.value = String(learnerId);
        $('learnerEdit').disabled = !learnerId; $('learningEmpty').hidden = Boolean(learnerId); $('learningContent').hidden = !learnerId;
        if (!learnerId) { message('点击“新建档案”，开始记录学习情况。'); return; }
        await refresh();
        await searchQuestions();
        restoreScan();
    }
    async function refresh() {
        if (!learnerId) return;
        const id = learnerId, token = serial;
        const [data, history, reviews, mistakes] = await Promise.all([request('learners/' + id),
            request(`learners/${id}/attempts?offset=${(historyPage - 1) * 20}`),
            request(`learners/${id}/review`), request(`learners/${id}/mistakes?limit=100`)]);
        if (!current(id, token)) return;
        if (detail?.id === id && detail.revision > data.revision) return;
        detail = data; reviewPlan = reviews; mistakePlan = mistakes;
        if (candidate && candidate.basis_revision !== data.revision) candidate = null;
        const stats = $('learningStats'); stats.replaceChildren();
        [['练习记录', data.attempts], ['已核对（含自动分析）', data.graded], ['待核对', data.ungraded],
         ['错题', data.open_mistakes], ['待复习', data.review_due], ['有记录的知识点', data.mastery.length]].forEach(([label, count]) => {
            const card = node('div', undefined, stats, 'glass-panel learning-stat'); node('strong', String(count), card); node('span', label, card);
        });
        message([data.grade, data.goal].filter(Boolean).join(' · ') || '尚未填写年级或学习目标，可在“编辑档案”中补充。');
        renderMastery(); renderHistory(history); renderReviewPlan(); renderMistakes();
        $('learningErrorTypes').textContent = (data.error_types || []).map(row => `${row.label} ${row.count}题`).join(' · ') || '暂无可归类的错误证据。';
        renderPracticeFocus(data);
        loadDaily();
        loadRecommendations().catch(error => { $('learningRecommendations').textContent = error.message; });
        if (!candidate) drawProfile(data.ai_profile?.report ? data.ai_profile : null, false, data.profile_stale);
        profileTask = recalledTask(); $('learningReconnectProfile').hidden = !profileTask;
        const updating = ['queued', 'processing'].includes(data.profile_update_state);
        if (updating) $('learningProfileStatus').textContent = '记录与知识统计已更新，AI 正在后台重新总结犯错点；旧画像仅供历史参考。';
        else if (data.profile_error) $('learningProfileStatus').textContent = data.profile_error;
        else if (data.ai_profile?.updated_by === 'automatic') $('learningProfileStatus').textContent = 'AI 已根据最新核对记录更新犯错点与学习建议。发现误判可点击“纠正画像”。';
        clearTimeout(automaticTimer);
        if (updating) automaticTimer = setTimeout(() => {
            if (current(id, token) && document.body.dataset.workspace === 'learning') refresh().catch(error => message(error.message));
        }, 2000);
    }
    function renderMastery() {
        const host = $('learningMastery'); host.replaceChildren();
        const query = $('learningMasteryQuery').value.trim().toLocaleLowerCase();
        const rows = (detail?.mastery || []).filter(row => (row.subject + ' ' + row.name).toLocaleLowerCase().includes(query));
        if (!rows.length) node('p', query ? '没有匹配的知识点。' : '暂无可统计的知识点。请给题目标注知识点，并核对作答结果。', host, 'text-sm text-slate-500');
        rows.slice(0, masteryLimit).forEach(row => {
            const card = node('div', undefined, host, 'learning-mastery-row learning-stack');
            node('strong', `${row.education_stage || '未标注'} · ${row.subject} · ${row.name}`, card, 'text-sm');
            node('p', `${row.status} · 核对得分率 ${row.score_rate}% · ${row.sample_count} 道不同题`, card, 'text-xs text-slate-500');
            const progress = node('progress', undefined, card); progress.max = 100; progress.value = row.score_rate;
            progress.setAttribute('aria-label', `${row.name}：${row.sample_count}道不同题的核对得分率${row.score_rate}%`);
            const date = new Date(row.last_practiced_at);
            node('small', `累计核对 ${row.attempt_count} 次 · 最近 ${date.toLocaleDateString()}${Date.now() - date > 30 * 86400000 ? ' · 30天未练习' : ''}`, card, 'text-xs text-slate-500');
            const button = node('button', '查看最近一条依据', card, 'glass-btn rounded-lg px-2 py-1 text-xs'); button.type = 'button';
            button.addEventListener('click', action(() => openAttempt(row.evidence_ids[0])));
        });
        $('learningMasteryMore').hidden = rows.length <= masteryLimit;
    }
    function renderHistory(data) {
        const host = $('learningHistory'); host.replaceChildren();
        if (!data.items.length) node('p', '还没有作答记录。', host, 'text-sm text-slate-500');
        data.items.forEach(row => {
            const button = node('button', `${row.subject} · ${outcomes[row.outcome]} · ${row.assessment_source === 'scan_ai' ? '扫描作答' : row.origin === 'manual' ? '手工录入' : '在线练习'}`, host, 'learning-row'); button.type = 'button';
            node('small', row.content_excerpt, button); node('small', new Date(row.created_at).toLocaleString(), button);
            button.addEventListener('click', action(() => openAttempt(row.id)));
        });
        $('learningHistoryPage').textContent = `第 ${historyPage} 页 · 共 ${data.total} 条`;
        $('learningHistoryPrev').disabled = historyPage <= 1; $('learningHistoryNext').disabled = historyPage * 20 >= data.total;
    }
    function renderReviewPlan() {
        const host = $('learningReviewList'); host.replaceChildren();
        const rows = reviewPlan?.items || [];
        $('learningReviewDue').textContent = rows.length ? `到期 ${reviewPlan.total} 题` : '今天没有到期题目';
        $('learningStartReview').disabled = !rows.some(row => row.question_id);
        if (!rows.length) node('p', '完成核对或录入扫描分析后，这里会按日期安排回顾。', host, 'text-sm text-slate-500');
        rows.forEach(row => {
            const card = node('div', undefined, host, 'learning-row');
            node('strong', `${row.subject || '未知学科'} · ${row.content_excerpt}`, card, 'text-sm');
            node('small', `原题 #${row.question_id ?? '已删除'} · 间隔 ${row.interval_days} 天 · 连续正确 ${row.streak} 次`, card, 'text-xs text-slate-500');
            if (row.question_id) {
                const button = node('button', '复习这道题', card, 'glass-btn rounded-lg px-2 py-1 text-xs mt-2'); button.type = 'button';
                button.addEventListener('click', action(() => chooseQuestion(row.question_id)));
            }
        });
    }
    function renderMistakes() {
        const host = $('learningMistakeList'); host.replaceChildren();
        const rows = mistakePlan?.items || [];
        $('learningMistakeCount').textContent = mistakePlan ? `共 ${mistakePlan.total} 道` : '';
        $('learningExportMistakes').disabled = !rows.some(row => row.question_id);
        $('learningExportMistakesAnswers').disabled = $('learningExportMistakes').disabled;
        if (!rows.length) node('p', '还没有未攻克的错题。核对为错误或部分正确后会出现在这里。', host, 'text-sm text-slate-500');
        rows.forEach(row => {
            const card = node('div', undefined, host, 'learning-row');
            node('strong', `${row.subject} · ${outcomes[row.outcome]} · 错 ${row.wrong_count} 次`, card, 'text-sm');
            node('small', row.content_excerpt, card);
            const toolbar = node('div', undefined, card, 'learning-toolbar mt-2');
            if (row.question_id) {
                const again = node('button', '再练一次', toolbar, 'glass-btn rounded-lg px-2 py-1 text-xs'); again.type = 'button';
                again.addEventListener('click', action(() => chooseQuestion(row.question_id)));
            }
            const view = node('button', '查看记录', toolbar, 'glass-btn rounded-lg px-2 py-1 text-xs'); view.type = 'button';
            view.addEventListener('click', action(() => openAttempt(row.id)));
        });
    }
    function renderExplanation(advice) {
        const host = $('learningExplanation'); host.hidden = false; host.replaceChildren();
        if (!advice) return;
        node('p', 'AI 错因建议 · 未写入学习记录', host, 'font-semibold text-sm');
        node('p', '错因概要：' + advice.error_summary, host, 'text-sm whitespace-pre-wrap');
        node('p', '易错点：' + advice.error_point, host, 'text-sm whitespace-pre-wrap');
        node('p', '改进建议：' + advice.suggestion, host, 'text-sm whitespace-pre-wrap');
        if (advice.variant) node('p', '练习方向：' + advice.variant, host, 'text-sm whitespace-pre-wrap');
        node('p', advice.advisory || 'AI 生成，仅供对照原答案核对。', host, 'text-xs text-slate-500');
        const save = node('button', '保存到核对备注', host, 'glass-btn rounded-lg px-2 py-1 text-xs'); save.type = 'button';
        save.style.alignSelf = 'flex-start';
        save.addEventListener('click', action(saveExplanation));
    }
    async function explain() {
        if (!review || saving) return;
        if (!['incorrect', 'partial'].includes(review.outcome)) { message('请先核对为错误或部分正确，再分析错因。'); return; }
        const id = learnerId, token = serial, identifier = review.id;
        const host = $('learningExplanation'); host.hidden = false; host.replaceChildren();
        node('p', '正在对照本题作答分析具体错误步骤，不会改动学习记录…', host, 'text-sm text-slate-500');
        $('learningExplain').disabled = true; $('learningExplain').setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(`/api/learning/learners/${id}/attempts/${identifier}/explain`, {method: 'POST'});
            const result = await response.json().catch(() => ({}));
            if (!current(id, token) || !review || review.id !== identifier) return;
            if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'AI 错因分析未完成，请稍后重试。');
            explanation = result; renderExplanation(result);
        } catch (error) {
            if (!current(id, token)) return;
            host.replaceChildren(); node('p', error.message || 'AI 错因分析未完成，请稍后重试。', host, 'text-sm text-red-600');
        } finally {
            if (current(id, token)) { $('learningExplain').removeAttribute('aria-busy'); $('learningExplain').disabled = false; }
        }
    }
    async function saveExplanation() {
        if (!review || !explanation || saving) return;
        const outcome = review.outcome, identifier = review.id, revision = review.revision;
        const text = ['【AI错因】' + explanation.error_summary, explanation.error_point,
            '建议：' + explanation.suggestion, explanation.variant ? '练习方向：' + explanation.variant : ''].filter(Boolean).join(' ');
        const notes = ($('learningReviewNotes').value.trim() ? $('learningReviewNotes').value.trim() + '\n' : '') + text;
        if (notes.length > 2000) { message('备注将超过2000字，请先精简核对备注再保存。'); return; }
        await mutate($('learningExplain'), async () => {
            showReview(await request(`learners/${learnerId}/attempts/${identifier}`, 'PATCH', {outcome, notes, revision}));
            explanation = null; $('learningExplanation').hidden = true; await refresh();
            message('错因建议已保存到核对备注，核对结果未改变。');
        });
    }
    function downloadBlob(blob, filename) {
        const url = URL.createObjectURL(blob); const link = document.createElement('a');
        link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    async function exportMistakes(withAnswers) {
        if (!learnerId || saving) return;
        const ids = (mistakePlan?.items || []).map(row => row.question_id).filter(value => Number.isInteger(value));
        if (!ids.length) { message('当前错题没有仍存在的题目可导出。'); return; }
        const id = learnerId, token = serial, name = detail?.name || '学习档案';
        const button = withAnswers ? $('learningExportMistakesAnswers') : $('learningExportMistakes');
        await mutate(button, async () => {
            const response = await fetch('/api/paper/export/pdf', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({title: `${name} · 错题复习卷`, paper_type: 'exam', questions: ids.map(value => ({id: value})),
                    include_answers: withAnswers, show_secret: false, show_notice: false})});
            if (!current(id, token)) return;
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.message || '错题卷导出失败，请稍后重试。');
            }
            downloadBlob(await response.blob(), `错题复习卷_${new Date().toISOString().slice(0, 10)}.pdf`);
            message(`错题卷已生成，共 ${ids.length} 题${withAnswers ? '（含答案）' : ''}。`);
        });
    }
    async function searchQuestions() {
        const id = learnerId, token = ++querySerial;
        const parameters = new URLSearchParams({page: questionPage, page_size: 10, q: $('learningQuery').value.trim()});
        if ($('learningEducationStage').value) parameters.set('education_stage', $('learningEducationStage').value);
        if ($('learningSubject').value) parameters.set('subject', $('learningSubject').value);
        const response = await fetch('/api/questions?' + parameters);
        if (!response.ok) throw new Error('题目查询失败，请重试');
        const page = await response.json();
        const rows = Array.isArray(page) ? page : page.items;
        if (!Array.isArray(rows)) throw new Error('题目列表格式无效，请刷新后重试');
        if (id !== learnerId || token !== querySerial) return;
        const host = $('learningQuestions'); host.replaceChildren();
        if (!rows.length) node('p', '没有找到题目。请调整条件，或先向题库导入题目。', host, 'text-sm text-slate-500');
        rows.forEach(row => {
            const button = node('button', `#${row.id} · ${row.education_stage || '未标注'} · ${row.subject || '数学'} · ${String(row.content || '').slice(0, 120)}`, host, 'learning-row'); button.type = 'button';
            button.addEventListener('click', action(() => chooseQuestion(row.id)));
        });
        $('learningQuestionsPage').textContent = `第 ${questionPage} 页`;
        $('learningQuestionsPrev').disabled = questionPage <= 1; $('learningQuestionsNext').disabled = rows.length < 10;
    }
    function syncOrigin() {
        const manual = $('learningOrigin').value === 'manual';
        $('learningManualFields').hidden = !manual;
        $('learningSubmit').textContent = manual ? '保存学习记录' : '提交作答并查看原答案';
        const choice = isChoiceQuestion();
        $('learningAnswer').required = !manual && !choice;
        $('learningAnswer').maxLength = choice ? 9950 : 10000;
        $('learningAnswerLabel').textContent = choice ? '解题步骤或补充说明（选填）' : '你的作答';
        $('learningAnswer').placeholder = choice ? '可补充解题思路，无需输入选项字母' : '填写答案与解题步骤，可使用 LaTeX';
    }
    function renderChoiceAnswer() {
        const host = $('learningChoiceOptions'); host.replaceChildren();
        const choice = isChoiceQuestion(); $('learningChoiceAnswer').hidden = !choice;
        if (!choice) return;
        const multiple = question.question_type === 'multi_choice';
        $('learningChoiceLegend').textContent = multiple ? '选择答案（可多选）' : '选择答案（单选）';
        // Reuse the preview's option labels; older text/image questions use A–D.
        const labels = [...$('learningQuestionBody').querySelectorAll('.choices-label')].map(item => item.textContent.trim().replace(/[.．、]$/, ''));
        const values = labels.length >= 2 && labels.length <= 8 && labels.every((value, index) => value === 'ABCDEFGH'[index]) ? labels : ['A', 'B', 'C', 'D'];
        values.forEach(value => {
            const label = node('label', undefined, host, 'learning-choice-option');
            const input = node('input', undefined, label); input.type = multiple ? 'checkbox' : 'radio'; input.name = 'learning-choice'; input.value = value;
            node('span', value, label);
        });
    }
    async function chooseQuestion(id, online = false) {
        if (saving || !mayReplace()) return;
        const owner = learnerId, token = serial;
        const data = await request('practice/' + id);
        if (!current(owner, token)) return;
        resetPractice(); question = data; started = performance.now(); requestId = crypto.randomUUID();
        if (online) $('learningOrigin').value = 'practice';
        $('learningAnswer').value = ''; $('learningNotes').value = ''; $('learningElapsed').value = '';
        $('learningManualOutcome').value = 'ungraded';
        $('learningQuestionMeta').textContent = `#${id} · ${data.subject} · ${data.topics.map(topic => topic.name).join('、') || '尚未标注知识点，记录后暂不计入知识点统计'}`;
        renderQuestionPreviewContent($('learningQuestionBody'), data.content);
        renderChoiceAnswer();
        $('learningAttemptForm').hidden = false; syncOrigin();
        showPanel('practice');
        $('learningQuestionMeta').focus({preventScroll: true});
        $('learningQuestionBody').scrollIntoView({block: 'nearest', behavior: 'smooth'});
    }
    async function submitAttempt(event) {
        event.preventDefault(); if (!question || !learnerId || saving) return;
        const manual = $('learningOrigin').value === 'manual';
        if (!pendingPayload && !manual && isChoiceQuestion() && !selectedChoices().length) {
            message('请先选择答案，再提交作答。'); $('learningChoiceOptions').querySelector('input')?.focus(); return;
        }
        if (pendingPayload && !window.confirm('上次提交结果尚未确认。将使用原请求标识查询/重试原作答，当前新修改尚不会提交。继续？')) return;
        const payload = pendingPayload || {request_id: requestId, question_id: question.question_id,
            question_revision: question.question_revision, student_answer: practiceAnswer(),
            notes: $('learningNotes').value, origin: manual ? 'manual' : 'practice',
            outcome: manual ? $('learningManualOutcome').value : 'ungraded', elapsed_seconds: manual
                ? ($('learningElapsed').value === '' ? null : Number($('learningElapsed').value))
                : Math.min(86400, Math.max(0, Math.round((performance.now() - started) / 1000)))};
        const id = learnerId;
        await mutate($('learningSubmit'), async () => {
            pendingPayload = payload;
            let result;
            try { result = await request(`learners/${id}/attempts`, 'POST', payload); }
            catch (error) { if ([400, 404, 409, 422].includes(error.status)) pendingPayload = null; throw error; }
            pendingPayload = null; question = null; $('learningAttemptForm').hidden = true;
            showReview(result); await refresh(); message('作答已保存。请对照原答案人工核对；待核对记录不计入知识掌握统计。');
        });
    }
    function showReview(row) {
        review = row; explanation = null; $('learningExplanation').hidden = true;
        $('learningReview').hidden = false;
        $('learningReviewMeta').textContent = `${row.subject} · ${new Date(row.created_at).toLocaleString()} · ${row.elapsed_seconds === null ? '未记录用时' : row.elapsed_seconds + '秒'} · ${row.question_id ? '当时的题目与答案快照' : '原题已删除，快照仍保留'}`;
        renderQuestionPreviewContent($('learningReviewQuestion'), row.content);
        $('learningReviewAnswer').textContent = row.student_answer || '（未填写作答文字）';
        renderQuestionPreviewContent($('learningReference'), row.reference_answer || '本题没有原答案，请本人或老师另行核对。');
        $('learningReviewOutcome').value = row.outcome; $('learningReviewNotes').value = row.notes;
        const types = $('learningReviewErrorTypes'); types.querySelectorAll('label').forEach(label => label.remove());
        Object.entries(errorTypes).forEach(([value, text]) => {
            const label = node('label', undefined, types); const input = node('input', undefined, label);
            input.type = 'checkbox'; input.value = value; input.checked = (row.error_types || []).includes(value); node('span', text, label);
        });
        const host = $('learningReviewAssessment'); host.replaceChildren();
        const assessment = row.assessment || {};
        if (assessment.source === 'scan_ai') {
            node('p', assessment.human_reviewed ? '已人工复核；以下保留原 AI 分析作为历史依据。' : 'AI 自动分析，可人工纠正；参考解答可能由 AI 生成。', host, 'text-xs text-slate-500');
            node('p', assessment.reason || '', host, 'text-sm whitespace-pre-wrap');
            node('p', assessment.suggestion || '', host, 'text-sm whitespace-pre-wrap');
            const evidence = node('details', undefined, host); node('summary', '查看扫描作答原图', evidence, 'cursor-pointer text-sm');
            (assessment.evidence_images || []).forEach((path, index) => {
                const url = window.StarHopeSafe.safeImageUrl(path);
                if (url) { const image = node('img', undefined, evidence); image.src = url; image.alt = `本次作答扫描原图 ${index + 1}`; image.loading = 'lazy'; }
            });
        }
    }
    async function openAttempt(identifier) {
        if (saving || !mayReplace()) return;
        const id = learnerId, token = serial;
        const row = await request(`learners/${id}/attempts/${identifier}`);
        if (!current(id, token)) return;
        resetPractice(); showReview(row); showPanel('practice');
        $('learningReviewMeta').focus({preventScroll: true});
        $('learningReview').scrollIntoView({block: 'nearest', behavior: 'smooth'});
    }
    async function grade() {
        if (!review) return;
        const id = learnerId, identifier = review.id;
        const payload = {outcome: $('learningReviewOutcome').value, notes: $('learningReviewNotes').value, revision: review.revision,
            error_types: [...$('learningReviewErrorTypes').querySelectorAll('input:checked')].map(input => input.value)};
        await mutate($('learningGrade'), async () => {
            showReview(await request(`learners/${id}/attempts/${identifier}`, 'PATCH', payload)); await refresh();
            message('人工核对结果已保存，知识掌握统计已更新。');
        });
    }
    function drawProfile(profile, suggested, stale = false) {
        const host = $('learningProfile'); host.replaceChildren(); $('learningApplyProfile').hidden = !suggested;
        if (!profile) { node('p', '有了已核对的知识点记录后，即可生成 AI 学习画像。', host, 'text-sm text-slate-500'); return; }
        node('p', suggested ? 'AI 画像建议 · 待确认保存' : stale ? '已保存画像 · 记录已变化，建议重新生成' : '已保存的 AI 画像建议', host, 'font-semibold text-sm');
        node('p', profile.report.summary, host, 'text-sm whitespace-pre-wrap');
        profile.report.insights.forEach(item => {
            const card = node('div', undefined, host, 'learning-row'); node('strong', item.subject + ' · ' + item.topic, card, 'text-sm');
            node('p', item.observation, card, 'text-sm whitespace-pre-wrap mt-2'); node('p', '下一步：' + item.action, card, 'text-sm whitespace-pre-wrap mt-2');
            if (item.error_pattern) node('p', '犯错点：' + item.error_pattern, card, 'text-sm whitespace-pre-wrap mt-2');
            item.evidence_ids.forEach(identifier => {
                const button = node('button', '查看依据 ' + identifier.slice(0, 8), card, 'glass-btn rounded-lg px-2 py-1 text-xs mt-2'); button.type = 'button';
                button.addEventListener('click', action(() => openAttempt(identifier)));
            });
        });
        profile.report.study_plan.forEach((value, index) => node('p', `${index + 1}. ${value}`, host, 'text-sm whitespace-pre-wrap'));
        node('p', `生成于 ${new Date(profile.generated_at).toLocaleString()} · ${profile.model} · 依据 ${profile.sample_count} 条核对记录（含自动分析，文本节选）`, host, 'text-xs text-slate-500');
    }
    async function pollProfile() {
        clearTimeout(profileTimer);
        const id = learnerId, token = serial, identifier = profileTask;
        if (!id || !identifier) return;
        const state = await request(`learners/${id}/profile-tasks/${identifier}`);
        if (!current(id, token) || identifier !== profileTask) return;
        const running = !['completed', 'error', 'cancelled', 'interrupted'].includes(state.status);
        $('learningGenerateProfile').disabled = running; $('learningCancelProfile').hidden = !running;
        if (state.status === 'completed') {
            candidate = state.profile; drawProfile(candidate, true);
            $('learningProfileStatus').textContent = '建议已生成，请查看依据后确认保存。新记录产生后，旧建议将不能保存。';
        } else if (running) {
            $('learningProfileStatus').textContent = '正在根据学习记录生成画像，可切换工作区；不会改动原始记录。';
            profileTimer = setTimeout(() => pollProfile().catch(profileError), 2000);
        } else {
            $('learningProfileStatus').textContent = state.error || '生成已取消，原有记录与画像仍保留。';
        }
    }
    function profileError(error) {
        $('learningProfileStatus').textContent = (error.message || '任务查询暂未完成') + '；可点击“接回本次画像任务”。';
        $('learningReconnectProfile').hidden = false; $('learningCancelProfile').hidden = true; $('learningGenerateProfile').disabled = false;
    }
    async function generateProfile() {
        if (!learnerId) return;
        if (profileTask && !window.confirm('已有本次画像任务记录。重新生成会创建一次新的模型请求，确定继续？')) return;
        const id = learnerId, token = serial;
        profileTask = crypto.randomUUID(); rememberTask(profileTask); candidate = null;
        $('learningGenerateProfile').disabled = true; $('learningReconnectProfile').hidden = false; $('learningApplyProfile').hidden = true;
        try {
            await request(`learners/${id}/profile-tasks`, 'POST', {request_id: profileTask});
            if (current(id, token)) await pollProfile();
        } catch (error) { if (current(id, token)) profileError(error); }
    }
    function editProfile(create) {
        if (saving || (!create && !detail)) return;
        const returnFocus = document.activeElement;
        if (!dialog) { dialog = node('dialog', undefined, document.body, 'glass-panel learning-dialog'); dialog.id = 'learnerDialog'; dialog.setAttribute('aria-labelledby', 'learnerDialogHeading'); }
        dialog.replaceChildren();
        const heading = node('h3', create ? '新建学习档案' : '编辑学习档案', dialog, 'font-semibold text-lg'); heading.id = 'learnerDialogHeading';
        const form = node('form', undefined, dialog, 'learning-stack mt-4');
        function input(labelText, value, maximum, textarea = false) {
            const label = node('label', labelText, form); const field = node(textarea ? 'textarea' : 'input', undefined, label, 'glass-input');
            field.value = value; field.maxLength = maximum; return field;
        }
        const name = input('名称或昵称', create ? '' : detail.name, 80); name.required = true;
        const grade = input('年级（可选）', create ? '' : detail.grade, 80);
        const goal = input('学习目标（可选）', create ? '' : detail.goal, 1000, true);
        const corrections = input('画像纠正说明（可选，可随时修改）', create ? '' : detail.profile_notes || '', 2000, true);
        corrections.placeholder = '例如：第2题已订正；请结合最新步骤核对，不要把计算失误直接归为概念不清。';
        const automaticLabel = node('label', undefined, form); automaticLabel.style.flexDirection = 'row'; automaticLabel.style.alignItems = 'center';
        const automatic = node('input', undefined, automaticLabel); automatic.type = 'checkbox'; automatic.style.width = '18px';
        automatic.checked = create ? true : detail.auto_profile === true;
        automaticLabel.append(document.createTextNode('自动更新 AI 画像与犯错点总结'));
        node('p', '档案只保存在本机，同机使用者可以切换查看。自动更新会在学习记录变化后调用已配置的分类 API；仅学习建议更新，原始作答和人工核对结果保持原记录。', form, 'text-xs text-slate-500');
        const status = node('p', '', form, 'text-sm text-red-600'); status.setAttribute('role', 'status');
        const buttons = node('div', undefined, form, 'learning-toolbar');
        const save = node('button', '保存档案', buttons, 'glass-btn px-3 py-2 rounded-xl'); save.type = 'submit';
        const close = node('button', '取消', buttons, 'glass-btn px-3 py-2 rounded-xl'); close.type = 'button'; close.onclick = () => dialog.close();
        const id = learnerId, revision = detail?.revision;
        if (!create) {
            const remove = node('button', '删除档案与其记录', buttons, 'glass-btn px-3 py-2 rounded-xl text-red-600'); remove.type = 'button';
            remove.addEventListener('click', async () => {
                if (!window.confirm(`将永久删除“${detail.name}”及其 ${detail.attempts} 条学习记录和画像。确定删除？`)) return;
                remove.disabled = true;
                try {
                    await request(`learners/${id}?revision=${revision}`, 'DELETE'); clearTimeout(profileTimer);
                    dialog.close(); serial++; resetPractice(); candidate = null; learnerId = 0; historyPage = 1; await loadProfiles();
                } catch (error) { status.textContent = error.message; remove.disabled = false; }
            });
        }
        form.addEventListener('submit', async event => {
            event.preventDefault(); save.disabled = true; close.disabled = true;
            try {
                if (create && !mayReplace()) { save.disabled = false; close.disabled = false; return; }
                const value = await request(create ? 'learners' : 'learners/' + id, create ? 'POST' : 'PUT',
                    {name: name.value, grade: grade.value, goal: goal.value, auto_profile: automatic.checked,
                        profile_notes: corrections.value, ...(create ? {} : {revision})});
                dialog.close(); serial++; candidate = null;
                if (create) { resetPractice(); historyPage = 1; }
                await loadProfiles(value.id);
            } catch (error) { status.textContent = error.message; save.disabled = false; close.disabled = false; }
        });
        dialog.addEventListener('close', () => returnFocus?.focus(), {once: true}); dialog.showModal(); name.focus();
    }
    function renderPracticeFocus(data) {
        $('learningProfileEvidence').textContent = `画像依据：${data.graded}条已核对记录，其中${data.automatic_graded || 0}条为尚未人工复核的扫描自动分析；${data.ungraded}条待核对不计入得分。`;
        const host = $('learningPracticeFocus'); host.replaceChildren();
        node('strong', '当前优先练习点', host, 'text-sm');
        if (!data.practice_focus?.length) node('p', data.mastery?.length ? '当前有充分证据的知识点表现较稳定，可通过每日题单继续复习和补充练习。' : '先完成并核对几道题，并标注知识点，逐步积累画像证据；可从每日题单开始。', host, 'text-sm text-slate-500');
        (data.practice_focus || []).forEach(item => {
            const card = node('div', undefined, host, 'learning-row');
            node('strong', `${item.education_stage} · ${item.subject} · ${item.topic}`, card, 'text-sm');
            node('p', `${item.sample_count}道不同题 · 最近得分率${item.score_rate}%${item.sample_count < 3 ? ' · 证据不足' : ''}`, card, 'text-xs text-slate-500');
            if (item.error_types?.length) node('p', '已记录错因：' + item.error_types.map(key => errorTypes[key] || key).join('、'), card, 'text-sm');
            node('p', item.action, card, 'text-sm');
            const evidence = node('button', '查看记录依据', card, 'glass-btn px-3 py-2 rounded-xl'); evidence.type = 'button';
            evidence.addEventListener('click', action(() => openAttempt(item.evidence_ids[0])));
        });
    }
    async function loadDaily(create = false) {
        if (!learnerId) return;
        const owner = learnerId, token = serial, generation = ++dailySerial;
        const scope = {education_stage: $('learningEducationStage').value, subject: $('learningSubject').value};
        dailyData = null;
        $('learningDailyStart').hidden = true;
        $('learningDailyGenerate').hidden = true;
        $('learningDailyProgress').value = 0;
        $('learningDailyStatus').textContent = create ? '正在安排今日题单…' : '正在读取今日进度…';
        $('learningDailyList').replaceChildren();
        try {
            const result = await request(`learners/${owner}/daily-practice` + (create ? '' : '?' + new URLSearchParams(scope)), create ? 'POST' : 'GET', create ? scope : undefined);
            if (!current(owner, token) || generation !== dailySerial) return;
            dailyData = result;
            const scopeText = `${result.education_stage || '全部学段'} · ${result.subject || '全部学科'}`;
            $('learningDailyStatus').textContent = `${result.date} · ${scopeText} · ` + (result.generated
                ? `已作答 ${result.completed}/${result.total} 题 · 已核对 ${result.graded} 题 · 正确 ${result.correct} 题${result.unavailable ? ` · ${result.unavailable}题资料变化或不可用` : ''}`
                : create ? '暂无可安排的题目。请调整范围或补充题库；今天已做过的题暂不重复安排。' : '点击“生成今日题单”，安排本次练习。');
            $('learningDailyGenerate').hidden = result.generated;
            $('learningDailyStart').hidden = !result.items.some(row => row.available && !row.completed);
            $('learningDailyStart').textContent = result.completed ? '继续今日练习' : '开始今日练习';
            $('learningDailyProgress').max = Math.max(1, result.total);
            $('learningDailyProgress').value = result.completed;
            const host = $('learningDailyList');
            result.items.forEach((row, index) => {
                const card = node('div', undefined, host, 'learning-row');
                const state = row.completed ? outcomes[row.outcome] : row.available ? '未作答' : '不可用';
                node('strong', `${index + 1}. #${row.id} · ${state}`, card, 'text-sm');
                node('p', row.content_excerpt, card, 'text-sm whitespace-pre-wrap');
                node('p', row.reason, card, 'text-xs text-slate-500 mt-2');
                const toolbar = node('div', undefined, card, 'learning-toolbar mt-2');
                if (row.available && !row.completed) {
                    const start = node('button', '练习这道题', toolbar, 'glass-btn px-3 py-2 rounded-xl'); start.type = 'button';
                    start.addEventListener('click', action(() => chooseQuestion(row.id, true)));
                }
                if (row.attempt_id || row.evidence_ids?.length) {
                    const evidence = node('button', row.attempt_id ? '查看作答并核对' : '查看推荐依据', toolbar, 'glass-btn px-3 py-2 rounded-xl'); evidence.type = 'button';
                    evidence.addEventListener('click', action(() => openAttempt(row.attempt_id || row.evidence_ids[0])));
                }
            });
            if (result.generated) node('p', result.note + (result.basis_stale ? ' 学习记录已更新，今日题单保留生成时的安排。' : '') + (result.total < 5 ? ' 当前符合条件的题目不足5道，已按实际数量安排。' : ''), host, 'text-xs text-slate-500');
        } catch (error) {
            if (current(owner, token) && generation === dailySerial) {
                $('learningDailyStatus').textContent = (error.message || '题单暂不可用') + '；再次生成会接回今天的同一题单。';
                $('learningDailyGenerate').hidden = false;
            }
        }
    }
    async function loadRecommendations() {
        if (!learnerId) return;
        const owner = learnerId, token = serial, generation = ++recommendationSerial;
        const filters = new URLSearchParams({education_stage: $('learningEducationStage').value, subject: $('learningSubject').value});
        const result = await request(`learners/${owner}/recommendations?` + filters);
        if (!current(owner, token) || generation !== recommendationSerial) return;
        const host = $('learningRecommendations'); host.replaceChildren();
        if (!result.items.length) node('p', '暂无符合当前范围的推荐题。先录入并核对作答、补充知识点，或调整练习筛选。', host, 'text-sm text-slate-500');
        result.items.forEach(row => {
            const card = node('div', undefined, host, 'learning-row');
            node('strong', `#${row.id} · ${row.education_stage} · ${row.subject}`, card, 'text-sm');
            node('p', row.content_excerpt, card, 'text-sm whitespace-pre-wrap');
            node('p', row.reason, card, 'text-xs text-slate-500 mt-2');
            const toolbar = node('div', undefined, card, 'learning-toolbar mt-2');
            const start = node('button', '练习这道题', toolbar, 'glass-btn px-3 py-2 rounded-xl'); start.type = 'button';
            start.addEventListener('click', action(() => chooseQuestion(row.id)));
            if (row.evidence_ids?.length) {
                const evidence = node('button', '查看推荐依据', toolbar, 'glass-btn px-3 py-2 rounded-xl'); evidence.type = 'button';
                evidence.addEventListener('click', action(() => openAttempt(row.evidence_ids[0])));
            }
        });
    }
    function scanStorageKey() { return 'starhope.learning.scan-task.' + learnerId; }
    function rememberScan() { try { scanTask ? sessionStorage.setItem(scanStorageKey(), scanTask) : sessionStorage.removeItem(scanStorageKey()); } catch (_) {} }
    function restoreScan() {
        clearTimeout(scanTimer);
        if (!scanTask) { try { scanTask = sessionStorage.getItem(scanStorageKey()) || ''; } catch (_) {} }
        $('learningScanReconnect').hidden = !scanTask;
        if (scanTask && !scanData) pollScan().catch(scanError);
    }
    function scanError(error) {
        $('learningScanStatus').textContent = (error.message || '扫描任务暂不可用') + '；请接回同一任务或查看作答历史，不要重复新建扫描。';
        $('learningScanReconnect').hidden = !scanTask;
    }
    async function startScan(event) {
        event.preventDefault();
        if (!learnerId || saving || (scanData && !scanSaved && !window.confirm('当前扫描结果尚未录入。确定开始另一份扫描？'))) return;
        const files = [...$('learningScanFiles').files];
        if (!files.length) return;
        if (scanTask && !scanSaved && !scanData) { await pollScan(); return; }
        const owner = learnerId, token = serial;
        clearTimeout(scanTimer); scanTask = crypto.randomUUID(); scanData = null; scanSaved = false; scanPending = null; rememberScan();
        $('learningScanReconnect').hidden = false; $('learningScanCancel').hidden = false;
        $('learningScanResults').replaceChildren(); $('learningScanWarnings').hidden = true; $('learningScanApply').hidden = true;
        $('learningScanStatus').textContent = '正在上传扫描文件；识别和分析将在后台继续。';
        const form = new FormData(); form.append('request_id', scanTask);
        form.append('subject', $('learningScanSubject').value); form.append('education_stage', $('learningScanStage').value);
        files.forEach(file => form.append('files', file));
        await mutate($('learningScanStart'), async () => {
            try { await request(`learners/${owner}/scan-tasks`, 'POST', form); }
            catch (error) {
                if ([400, 404, 409, 422, 429].includes(error.status)) { scanTask = ''; rememberScan(); }
                throw error;
            }
        });
        if (current(owner, token)) await pollScan();
    }
    async function pollScan() {
        if (!scanTask || !learnerId) return;
        clearTimeout(scanTimer);
        const owner = learnerId, token = serial, task = scanTask;
        const data = await request(`learners/${owner}/scan-tasks/${task}`);
        if (!current(owner, token) || task !== scanTask) return;
        $('learningScanReconnect').hidden = false; $('learningScanCancel').hidden = false;
        $('learningScanStatus').textContent = data.error || `${data.message || '正在排队'}${data.status === 'completed' ? '' : ' · ' + (data.progress || 0) + '%'}`;
        if (data.status === 'completed') {
            if (data.imported) {
                scanSaved = true; scanPending = null; $('learningScanApply').hidden = true;
                $('learningScanStatus').textContent = '本次扫描已录入。请在作答历史查看和纠正结果。';
                await refresh();
            } else if (!scanData && !scanSaved) { scanData = data; renderScan(data, owner, task); }
        } else if (!['error', 'cancelled'].includes(data.status) && document.body.dataset.workspace === 'learning') {
            scanTimer = setTimeout(() => pollScan().catch(scanError), 2000);
        }
    }
    function scanField(host, text, key, value, maximum, rows = 3) {
        const label = node('label', text, host);
        const input = node('textarea', undefined, label, 'glass-input');
        input.dataset.scanField = key; input.value = value; input.maxLength = maximum; input.rows = rows;
        return input;
    }
    function scanCheck(host, text, key, checked = false) {
        const label = node('label', undefined, host);
        const input = node('input', undefined, label); input.type = 'checkbox'; input.dataset.scanField = key; input.checked = checked;
        node('span', text, label); return input;
    }
    function renderScan(data, owner, task) {
        const warnings = $('learningScanWarnings'); warnings.hidden = !data.warnings?.length;
        const noteHost = warnings.querySelector('div'); noteHost.replaceChildren();
        (data.warnings || []).forEach(text => node('p', text, noteHost));
        const host = $('learningScanResults'); host.replaceChildren();
        data.records.forEach((row, index) => {
            const card = node('details', undefined, host, 'learning-row learning-scan-card'); card.dataset.scanRecord = row.record_id; card.open = index === 0;
            node('summary', `题${row.number || index + 1} · ${outcomes[row.grade.outcome]} · 第${row.start_page}–${row.end_page}页`, card, 'font-semibold');
            const body = node('div', undefined, card, 'learning-stack');
            const checks = node('div', undefined, body, 'learning-checks');
            scanCheck(checks, '录入这道题及本次作答', 'selected', row.complete);
            if (!row.complete) node('p', '跨页题尚未收全，默认不选中。请补全题干后再录入。', body, 'text-sm text-amber-700');
            if (row.recognition !== 'clear') node('p', '存在识别不清的内容，自动结果保留待核对。', body, 'text-sm text-amber-700');
            scanField(body, '题干（不含学生作答）', 'content', row.content, 20000, 4);
            scanField(body, '学生实际作答', 'student_answer', row.student_answer, 10000, 4);
            scanField(body, row.printed_answer || row.bank_reference ? '参考解答（AI分析，依据扫描或题库候选答案）' : '参考解答（AI生成，仅保存在本次个人作答记录）', 'reference_answer', row.grade.reference_answer, 20000, 3);
            scanField(body, '知识点建议（每行一个，可修正）', 'subject_topics', row.subject_topics, 2000, 2);
            const preview = node('details', undefined, body); node('summary', '查看识别题干预览', preview, 'cursor-pointer text-sm');
            const formula = node('div', undefined, preview, 'learning-preview'); renderQuestionPreviewContent(formula, row.content);
            node('p', `自动分析：${row.grade.reason}`, body, 'text-sm whitespace-pre-wrap');
            node('p', `错误类型：${row.grade.error_types.map(type => errorTypes[type] || type).join('、') || '暂无明确错误类型'}。改进：${row.grade.suggestion}`, body, 'text-sm whitespace-pre-wrap');
            if (row.grade.evidence) node('p', '作答依据：' + row.grade.evidence, body, 'text-sm whitespace-pre-wrap');
            const resultLabel = node('label', '本次记录的结果', body); const result = node('select', undefined, resultLabel, 'glass-select'); result.dataset.scanField = 'outcome';
            Object.entries(outcomes).forEach(([value, text]) => { const option = node('option', text, result); option.value = value; }); result.value = row.grade.outcome;
            const confirmations = node('div', undefined, body, 'learning-checks');
            scanCheck(confirmations, '我已人工核对上述作答与结果', 'human_reviewed');
            node('p', '修改题干、作答、参考解答或判定后，原自动判断会失效；未勾选人工核对时保存为待核对。', body, 'text-xs text-slate-500');
            if (row.figures.length) {
                const figures = node('div', undefined, body, 'learning-stack');
                row.figures.forEach((figure, i) => { const image = node('img', undefined, figures); image.src = `/api/learning/learners/${owner}/scan-tasks/${task}/images/${figure.id}`; image.alt = `候选题图${i + 1}，请核对没有手写作答或个人信息`; image.loading = 'lazy'; });
                scanCheck(confirmations, '确认这些题图不含学生作答或个人信息，并作为题图录入', 'include_figures');
            }
            if (row.figures_required) node('p', '此题依赖题图。未确认题图时将保留“题图待补充”，自动判定不计入画像统计。', body, 'text-xs text-slate-500');
            const original = node('details', undefined, body); node('summary', '对照扫描原页', original, 'cursor-pointer text-sm');
            for (let page = row.start_page; page <= row.end_page; page++) {
                const image = node('img', undefined, original); image.src = `/api/learning/learners/${owner}/scan-tasks/${task}/images/page-${page}`; image.alt = `作答扫描第${page}页`; image.loading = 'lazy';
            }
        });
        $('learningScanApply').hidden = false;
        $('learningScanStatus').textContent = `${data.education_stage} · ${data.subject} · 识别 ${data.records.length} 题。核对后点击“录入选中题目”，同题将复用题库已有题目。`;
    }
    async function applyScan() {
        if (!scanData || !scanTask || scanSaved || saving) return;
        const items = [...$('learningScanResults').querySelectorAll('[data-scan-record]')].filter(card => card.querySelector('[data-scan-field="selected"]').checked).map(card => {
            const item = {record_id: card.dataset.scanRecord};
            card.querySelectorAll('[data-scan-field]').forEach(input => {
                if (input.dataset.scanField !== 'selected') item[input.dataset.scanField] = input.type === 'checkbox' ? input.checked : input.value;
            }); return item;
        });
        if (!items.length) { $('learningScanStatus').textContent = '请至少选择一道题。'; return; }
        const payload = scanPending || {items};
        const owner = learnerId, token = serial, task = scanTask;
        await mutate($('learningScanApply'), async () => {
            scanPending = payload;
            let result;
            try { result = await request(`learners/${owner}/scan-tasks/${task}/apply`, 'POST', payload); }
            catch (error) { if ([400, 404, 409, 422].includes(error.status)) scanPending = null; throw error; }
            if (!current(owner, token) || task !== scanTask) return;
            scanSaved = true; scanPending = null; $('learningScanApply').hidden = true;
            const pending = result.items.filter(row => row.outcome === 'ungraded').length;
            $('learningScanStatus').textContent = `已保存 ${result.items.length} 条作答，其中 ${pending} 条待核对。错题本、知识统计与推荐已更新，自动画像在后台继续。`;
            historyPage = 1; await refresh(); await searchQuestions();
        });
    }
    function initialize() {
        initialized = true;
        document.querySelectorAll('[data-learning-section]').forEach(button => {
            button.addEventListener('click', () => showPanel(button.dataset.learningSection));
        });
        [...$('learningSubject').options].filter(option => option.value).forEach(option => $('learningScanSubject').append(option.cloneNode(true)));
        $('learningScanSubject').value = '数学';
        $('learningScanForm').addEventListener('submit', action(startScan));
        $('learningScanReconnect').addEventListener('click', action(pollScan));
        $('learningScanApply').addEventListener('click', action(applyScan));
        $('learningRecommend').addEventListener('click', action(loadRecommendations));
        $('learningDailyGenerate').addEventListener('click', action(() => mutate($('learningDailyGenerate'), () => loadDaily(true))));
        $('learningDailyStart').addEventListener('click', action(() => {
            const next = dailyData?.items.find(row => row.available && !row.completed);
            return next ? chooseQuestion(next.id, true) : null;
        }));
        $('learningScanCancel').addEventListener('click', action(async () => {
            if (!scanTask || !window.confirm('取消或丢弃本次尚未录入的扫描结果？已保存的学习记录保留。')) return;
            try { await request(`learners/${learnerId}/scan-tasks/${scanTask}`, 'DELETE'); }
            catch (error) { if (error.status !== 404) throw error; }
            clearTimeout(scanTimer); scanTask = ''; scanData = null; scanPending = null; scanSaved = false; rememberScan();
            $('learningScanResults').replaceChildren(); $('learningScanApply').hidden = true; $('learningScanCancel').hidden = true; $('learningScanReconnect').hidden = true;
            $('learningScanStatus').textContent = '扫描已取消。';
        }));
        $('learnerCreate').addEventListener('click', () => editProfile(true)); $('learnerEdit').addEventListener('click', () => editProfile(false));
        $('learningCorrectProfile').addEventListener('click', () => editProfile(false));
        $('learnerSelect').addEventListener('change', action(async () => {
            const id = Number($('learnerSelect').value);
            if (!mayReplace()) { $('learnerSelect').value = String(learnerId); return; }
            clearTimeout(profileTimer); clearTimeout(scanTimer); scanTask = ''; scanData = null; scanPending = null; scanSaved = false;
            $('learningScanResults').replaceChildren(); $('learningScanStatus').textContent = ''; $('learningScanApply').hidden = true;
            serial++; learnerId = id; historyPage = 1; candidate = null; resetPractice();
            dailyData = null; dailySerial++; $('learningDailyList').replaceChildren(); $('learningDailyStart').hidden = true;
            $('learningDailyGenerate').hidden = true; $('learningDailyProgress').value = 0; $('learningDailyStatus').textContent = '正在读取当前档案…';
            $('learningProfileStatus').textContent = ''; $('learningApplyProfile').hidden = true; $('learningCancelProfile').hidden = true;
            $('learningGenerateProfile').disabled = false; await refresh(); restoreScan();
        }));
        $('learningQuestionSearch').addEventListener('submit', action(event => { event.preventDefault(); questionPage = 1; return searchQuestions(); }));
        $('learningEducationStage').addEventListener('change', action(() => { questionPage = 1; return Promise.all([searchQuestions(), loadRecommendations(), loadDaily()]); }));
        $('learningSubject').addEventListener('change', action(() => { questionPage = 1; return Promise.all([searchQuestions(), loadRecommendations(), loadDaily()]); }));
        $('learningQuestionsPrev').addEventListener('click', action(() => { questionPage--; return searchQuestions(); }));
        $('learningQuestionsNext').addEventListener('click', action(() => { questionPage++; return searchQuestions(); }));
        $('learningOrigin').addEventListener('change', syncOrigin);
        $('learningAttemptForm').addEventListener('submit', action(submitAttempt)); $('learningGrade').addEventListener('click', action(grade));
        $('learningExplain').addEventListener('click', action(explain));
        $('learningStartReview').addEventListener('click', action(() => {
            const first = (reviewPlan?.items || []).find(row => row.question_id);
            return first ? chooseQuestion(first.question_id) : null;
        }));
        $('learningExportMistakes').addEventListener('click', action(() => exportMistakes(false)));
        $('learningExportMistakesAnswers').addEventListener('click', action(() => exportMistakes(true)));
        $('learningDeleteAttempt').addEventListener('click', action(async () => {
            if (!review || !window.confirm('确定删除这条学习记录？知识统计将重新计算，原有画像会标记为待更新。')) return;
            const id = learnerId, record = review;
            await mutate($('learningDeleteAttempt'), async () => { await request(`learners/${id}/attempts/${record.id}?revision=${record.revision}`, 'DELETE'); review = null; $('learningReview').hidden = true; historyPage = 1; await refresh(); });
        }));
        $('learningHistoryPrev').addEventListener('click', action(() => { historyPage--; return refresh(); }));
        $('learningHistoryNext').addEventListener('click', action(() => { historyPage++; return refresh(); }));
        $('learningMasteryQuery').addEventListener('input', () => { masteryLimit = 100; renderMastery(); });
        $('learningMasteryMore').addEventListener('click', () => { masteryLimit += 100; renderMastery(); });
        $('learningGenerateProfile').addEventListener('click', action(generateProfile));
        $('learningReconnectProfile').addEventListener('click', () => { profileTask = recalledTask(); pollProfile().catch(profileError); });
        $('learningCancelProfile').addEventListener('click', action(async () => { if (profileTask) { await request(`learners/${learnerId}/profile-tasks/${profileTask}/cancel`, 'POST'); await pollProfile(); } }));
        $('learningApplyProfile').addEventListener('click', action(async () => {
            if (!candidate || !profileTask) return;
            await mutate($('learningApplyProfile'), async () => {
                await request(`learners/${learnerId}/profile-tasks/${profileTask}/apply`, 'POST'); candidate = null; rememberTask(''); profileTask = ''; await refresh(); $('learningProfileStatus').textContent = '画像建议已确认保存。';
            });
        }));
    }
    return {receivePhoto: file => {
        if (!initialized || saving) { showToast('请等待学习档案加载或保存完成。','info'); return false; }
        const input = $('learningScanFiles');
        if (input.files.length && !confirm('替换当前尚未提交的扫描文件？已有扫描结果会保留。')) return false;
        const transfer = new DataTransfer(); transfer.items.add(file); input.files = transfer.files;
        showPanel('scan'); input.focus();
        $('learningScanStatus').textContent = '手机照片已带入，请确认学习档案与学段后点击开始扫描。';
        return true;
    }, open: () => {
        if (!initialized) { initialize(); loadProfiles().catch(error => message(error.message)); }
        else if (learnerId) { refresh().catch(error => message(error.message)); restoreScan(); }
        else loadProfiles().catch(error => message(error.message));
    }};
})();
