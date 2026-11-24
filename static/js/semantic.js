// Semantic neighbours and typed question relations for the bank detail panel.
// Similarity is advisory recall only; it never merges variant groups.
(() => {
    const RELATION_LABELS = {
        variant_of: '变式', special_case_of: '特例',
        same_method: '同解法', prerequisite: '前置', related: '相关'
    };
    let currentQuestionId = null;
    let similarRequest = 0;

    const elements = () => ({
        wrapper: document.getElementById('paperRelationsWrapper'),
        list: document.getElementById('paperRelationsList'),
        status: document.getElementById('paperRelationsStatus'),
        button: document.getElementById('findSimilarBtn')
    });

    function note(text, tone) {
        const { status } = elements();
        if (!status) return;
        status.textContent = text;
        status.style.color = tone === 'error' ? 'rgb(190, 40, 40)' : '';
    }

    function nodeList() {
        const { list } = elements();
        if (!list) return null;
        list.textContent = '';
        return list;
    }

    function row(children, border = true) {
        const line = document.createElement('div');
        line.className = 'semantic-question-row rounded-lg px-2 py-1.5 text-[11px] leading-relaxed';
        if (border) {
            line.style.border = '1px solid rgba(148, 163, 184, .35)';
            line.style.background = 'rgba(255, 255, 255, .55)';
        }
        children.forEach(child => line.appendChild(child));
        return line;
    }

    function head(children) {
        const line = document.createElement('div');
        line.className = 'semantic-question-row-head';
        children.forEach(child => line.appendChild(child));
        return line;
    }

    function questionPreview(text) {
        const preview = document.createElement('div');
        preview.className = 'semantic-question-preview';
        const source = String(text || '');
        if (typeof window.renderQuestionPreviewContent === 'function') {
            try {
                window.renderQuestionPreviewContent(preview, source, { includeImages: false });
                return preview;
            } catch (_) { /* fall back to plain text below */ }
        }
        preview.textContent = source;
        return preview;
    }

    function chip(label) {
        const span = document.createElement('span');
        span.className = 'font-bold';
        span.style.marginRight = '6px';
        span.style.color = 'rgb(109, 40, 217)';
        span.textContent = label;
        return span;
    }

    function actionButton(label, onClick) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'glass-btn text-[10px] rounded-md px-2 py-1';
        button.style.marginLeft = '6px';
        button.textContent = label;
        button.addEventListener('click', onClick);
        return button;
    }

    function label(node, fallbackId) {
        return node && node.seq_num != null ? `#${node.seq_num}` : `#${fallbackId}`;
    }

    function indexQuestionNodes(nodes) {
        const byId = {};
        (nodes || []).forEach(node => { byId[node.id] = node; });
        return byId;
    }

    async function readJson(response) {
        let payload = null;
        try { payload = await response.json(); } catch (_) { payload = null; }
        if (!response.ok) {
            const error = new Error((payload && (payload.detail || payload.message)) || `HTTP ${response.status}`);
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    async function createRelation(targetId, relationType) {
        const questionId = currentQuestionId;
        if (!questionId) return;
        const form = new FormData();
        form.append('target_id', String(targetId));
        form.append('relation_type', relationType);
        try {
            await readJson(await fetch(`/api/questions/${questionId}/relations`, {
                method: 'POST', body: form
            }));
            if (typeof showToast === 'function') showToast('已标注题目关系', 'success');
            await loadRelations();
        } catch (error) {
            if (typeof showToast === 'function') showToast(error.message, 'error');
        }
    }

    function renderRelations(payload) {
        const list = nodeList();
        if (!list) return;
        const nodes = indexQuestionNodes(payload.nodes);
        const edges = payload.edges || [];
        if (!edges.length) {
            note('暂无已标注关系。可先“找相似”，确认后再标注变式或同解法。');
            return;
        }
        note(`已标注 ${edges.length} 条关系；相似链不会并入变式分组。`);
        edges.forEach(edge => {
            const otherId = edge.source_id === currentQuestionId ? edge.target_id : edge.source_id;
            const arrow = edge.source_id === currentQuestionId ? '→' : '←';
            const text = document.createElement('span');
            text.textContent = `${arrow} ${label(nodes[otherId], otherId)}`;
            const kind = chip(RELATION_LABELS[edge.relation_type] || edge.relation_type);
            const remove = actionButton('移除', async () => {
                try {
                    await readJson(await fetch(`/api/question-relations/${edge.id}`, { method: 'DELETE' }));
                    await loadRelations();
                } catch (error) {
                    if (typeof showToast === 'function') showToast(error.message, 'error');
                }
            });
            list.appendChild(row([head([kind, text, remove]), questionPreview(nodes[otherId]?.content)]));
        });
    }

    async function loadRelations() {
        const questionId = currentQuestionId;
        if (!questionId) return;
        try {
            const payload = await readJson(await fetch(`/api/questions/${questionId}/relations?depth=1`));
            if (questionId !== currentQuestionId) return;
            renderRelations(payload);
        } catch (error) {
            note(`关系加载失败：${error.message}`, 'error');
        }
    }

    function renderSimilarItems(results, indexed) {
        const list = nodeList();
        if (!list) return;
        if (!results.length) {
            note(`没有达到相似度要求的同学科、同学段候选（已索引 ${Number(indexed) || 0} 题）。`);
            return;
        }
        results.forEach(item => {
            const text = document.createElement('span');
            text.textContent = `${label(item, item.id)} · 相似度 ${Number(item.score).toFixed(3)}`;
            const variant = actionButton('标为变式', () => createRelation(item.id, 'variant_of'));
            const method = actionButton('同解法', () => createRelation(item.id, 'same_method'));
            list.appendChild(row([head([text, variant, method]), questionPreview(item.content)]));
        });
    }

    async function reindex() {
        try {
            const payload = await readJson(await fetch('/api/semantic/reindex?limit=50', { method: 'POST' }));
            note(`已加入 ${payload.scheduled} 道题的索引队列，稍后重试“找相似”。`);
        } catch (error) {
            note(`建立索引失败：${error.message}`, 'error');
        }
    }

    function offerReindex(message) {
        const list = nodeList();
        note(message);
        if (!list) return;
        list.appendChild(row([actionButton('建立向量索引', reindex)], false));
    }

    async function findSimilarQuestions() {
        const questionId = currentQuestionId;
        const { button } = elements();
        if (!questionId || !button) return;
        const request = ++similarRequest;
        button.disabled = true;
        try {
            const response = await fetch(`/api/semantic/similar/${questionId}?limit=8`);
            if (request !== similarRequest) return;
            if (response.status === 503) {
                offerReindex('未配置向量服务；题库功能不受影响。');
                return;
            }
            const payload = await readJson(response);
            if (request !== similarRequest || questionId !== currentQuestionId) return;
            note(payload.score_note || '相似度只用于召回排序，不代表重复判定。');
            renderSimilarItems(payload.results || [], payload.indexed);
        } catch (error) {
            note(`相似题查询失败：${error.message}`, 'error');
        } finally {
            if (request === similarRequest) button.disabled = false;
        }
    }

    function resetPanel() {
        similarRequest += 1;
        const { wrapper, list, status } = elements();
        if (wrapper) wrapper.hidden = true;
        if (list) list.textContent = '';
        if (status) status.textContent = '相似度只用于召回排序，不代表重复判定。';
    }

    window.addEventListener('starhope:question-opened', event => {
        currentQuestionId = event.detail && event.detail.questionId != null
            ? Number(event.detail.questionId) : null;
        if (!currentQuestionId) {
            resetPanel();
            return;
        }
        const { wrapper } = elements();
        if (wrapper) wrapper.hidden = false;
        loadRelations();
    });

    window.findSimilarQuestions = findSimilarQuestions;
})();
