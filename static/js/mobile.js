(() => {
    'use strict';
    function takeQrTicket() {
        const ticket = new URLSearchParams(location.hash.slice(1)).get('pair') || '';
        if (location.hash) history.replaceState(null, '', location.pathname + location.search);
        return ticket;
    }
    const qrTicket = takeQrTicket();
    const $ = id => document.getElementById(id);
    let token = sessionStorage.getItem('starhope-mobile-token') || '';
    let file = null, identity = '', preview = '', page = 1, serial = 0, busy = false, limit = 0;
    const say = value => { $('status').textContent = value; };
    function connected(value) { $('pairPanel').hidden = value; $('connected').hidden = !value; }
    async function request(path, options = {}) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), path.startsWith('/api/photos') ? 600000 : 30000);
        try {
            const headers = new Headers(options.headers);
            if (token) headers.set('Authorization', 'Bearer ' + token);
            const response = await fetch(path, {...options, headers, signal: controller.signal});
            const value = await response.json();
            if (response.status === 401) { token = ''; sessionStorage.removeItem('starhope-mobile-token'); connected(false); }
            if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '操作失败，请检查输入后重试。');
            return value;
        } finally { clearTimeout(timer); }
    }
    function action(fn) { return event => { event.preventDefault(); Promise.resolve().then(() => fn(event)).catch(error => say(error.name === 'AbortError' ? '连接超时，请保留照片后重试。' : error.message)); }; }
    let pairing = false;
    async function pairDevice(payload) {
        if (pairing) return;
        pairing = true;
        const button = $('pairForm').querySelector('button'); button.disabled = true;
        try {
            const data = await request('/api/pair', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
            token = data.token; sessionStorage.setItem('starhope-mobile-token', token); $('code').value = '';
            limit = (await request('/api/connection')).max_image_bytes;
            connected(true); say('已连接电脑。可以拍照传送，或查看共享题库。');
        } finally { button.disabled = false; pairing = false; }
    }
    $('pairForm').addEventListener('submit', action(() => pairDevice({code:$('code').value.trim()})));
    $('disconnect').addEventListener('click', action(async () => { if (busy) return; await request('/api/disconnect', {method:'POST', body:''}); serial++; token = ''; sessionStorage.removeItem('starhope-mobile-token'); connected(false); say('已断开此页面，电脑收件箱中的照片仍保留。'); }));
    for (const [button, target] of [['photoTab','photoPanel'], ['bankTab','bankPanel']]) {
        $(button).addEventListener('click', () => {
            for (const id of ['photoPanel','bankPanel']) $(id).hidden = id !== target;
            $('photoTab').setAttribute('aria-pressed', String(target === 'photoPanel')); $('bankTab').setAttribute('aria-pressed', String(target === 'bankPanel'));
            if (target === 'bankPanel') loadBank().catch(error => say(error.message));
        });
    }
    function select(input) {
        if (busy || !input.files[0]) return;
        const next = input.files[0]; input.value = '';
        if (!next.type.startsWith('image/')) { say('请选择照片。'); return; }
        if (limit && next.size > limit) { say(`照片过大，上限 ${Math.floor(limit/1048576)} MiB，请降低相机分辨率后重试。`); return; }
        if (preview) URL.revokeObjectURL(preview);
        file = next;
        // Native randomUUID requires HTTPS on some Android browsers; HTTP LAN uses getRandomValues.
        const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
        const hex = [...bytes].map(value => value.toString(16).padStart(2,'0')).join('');
        identity = `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
        preview = URL.createObjectURL(file); $('preview').src = preview; $('preview').hidden = false;
        $('photoInfo').textContent = `${file.name} · ${(file.size/1048576).toFixed(1)} MiB`; $('send').disabled = false; say('照片已选择，请确认清晰后传给电脑。');
    }
    $('camera').addEventListener('change', () => select($('camera')));
    $('album').addEventListener('change', () => select($('album')));
    $('send').addEventListener('click', action(async () => {
        if (busy || !file) return;
        busy = true; $('send').disabled = true; $('camera').disabled = true; $('album').disabled = true;
        say('正在传送，请保持本页开启…');
        try {
            const form = new FormData(); form.append('file', file);
            await request('/api/photos?identity=' + encodeURIComponent(identity), {method:'POST', body:form});
            const row = document.createElement('li'); row.textContent = '电脑已接收 · ' + new Date().toLocaleTimeString() + ' · ' + file.name; $('receipts').prepend(row);
            file = null; identity = ''; $('preview').hidden = true; $('photoInfo').textContent = '';
            if (preview) URL.revokeObjectURL(preview); preview = '';
            say('电脑已接收并保存照片，请在电脑「手机互联」中校对录入。可以继续拍下一题。');
        } finally { busy = false; $('send').disabled = !file; $('camera').disabled = false; $('album').disabled = false; }
    }));
    async function loadBank() {
        const generation = ++serial;
        const result = await request('/api/library?q=' + encodeURIComponent($('query').value.trim()) + '&page=' + page);
        if (generation !== serial || !token) return;
        $('questions').replaceChildren();
        for (const item of result.items) {
            const article = document.createElement('article'), title = document.createElement('h3'), body = document.createElement('pre');
            title.textContent = `#${item.id} · ${item.education_stage || '未标注'} · ${item.subject || '数学'}`;
            body.textContent = item.content;
            const detail = document.createElement('details'), summary = document.createElement('summary'), answer = document.createElement('pre');
            summary.textContent = '查看答案与来源'; answer.textContent = (item.answer_markdown || '暂无答案') + '\n\n' + (item.source || '未标注来源');
            detail.append(summary, answer); article.append(title, body, detail); $('questions').append(article);
        }
        $('bankSummary').textContent = `共 ${result.total} 题 · 第 ${page} 页（题文原文，含 LaTeX 标记）`;
        $('prev').disabled = page <= 1; $('next').disabled = !result.next_page;
    }
    $('searchForm').addEventListener('submit', action(() => { page = 1; return loadBank(); }));
    $('prev').addEventListener('click', action(() => { page = Math.max(1,page-1); return loadBank(); }));
    $('next').addEventListener('click', action(() => { page++; return loadBank(); }));
    window.addEventListener('beforeunload', event => { if (file || busy) { event.preventDefault(); event.returnValue = ''; } });
    connected(false);
    $('pairForm').querySelector('button').disabled = false;
    document.body.dataset.mobileReady = 'true';
    async function connectQr(ticket) {
        if (pairing) return;
        // A scanner may reuse this open page; if it is already connected, the ticket is moot.
        if (token) {
            try {
                limit = (await request('/api/connection')).max_image_bytes;
                connected(true); say('本页已连接电脑，无需重复扫码。');
                return;
            } catch (error) { /* 401 already cleared token; retry pairing below. */ }
        }
        say('正在扫码连接电脑…');
        pairDevice({ticket}).catch(error => { say(error.name === 'AbortError' ? '扫码连接超时，请在电脑更换二维码后重试，或手动配对。' : error.message); $('code').focus(); });
    }
    // Scanners that reuse an open tab may not navigate; re-check on focus and bfcache restore.
    function scanTicket() { const ticket = takeQrTicket(); if (ticket) connectQr(ticket); }
    window.addEventListener('hashchange', scanTicket);
    window.addEventListener('pageshow', scanTicket);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) scanTicket(); });
    if (qrTicket) connectQr(qrTicket);
    else if (token) request('/api/connection').then(data => { limit=data.max_image_bytes; connected(true); say('已恢复与电脑的连接。'); }).catch(error => say(error.message));
})();
