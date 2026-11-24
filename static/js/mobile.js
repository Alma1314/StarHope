(() => {
    'use strict';
    function takeQrTicket() {
        const ticket = new URLSearchParams(location.hash.slice(1)).get('pair') || '';
        if (location.hash) history.replaceState(null, '', location.pathname + location.search);
        return ticket;
    }
    const qrTicket = takeQrTicket();
    const $ = id => document.getElementById(id);
    const DEFAULT_LIMIT = 10485760;
    let token = sessionStorage.getItem('starhope-mobile-token') || '';
    let file = null, identity = '', preview = '', page = 1, serial = 0, busy = false, limit = 0, selectSerial = 0;
    const say = value => { $('status').textContent = value; };
    function connected(value) { $('pairPanel').hidden = value; $('connected').hidden = !value; }

    // Phone photo errors: keep the cause understandable on a LAN page.
    function friendlyError(error) {
        if (error && error.name === 'AbortError') return '连接超时，请保留照片或重试。';
        if (error instanceof TypeError) return '连不上电脑：请确认手机与电脑连接同一 Wi-Fi、关闭手机移动数据，并允许电脑防火墙访问专用网络。';
        return (error && error.message) || '操作失败，请重试。';
    }

    // Shared questions are read-only on the phone: render the same Markdown and
    // math as the desktop. Raw source stays in the desktop editor only.
    function protectMobileLiterals(source) {
        const stash = [];
        const text = String(source || '').replace(
            /```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|<(?:code|pre)\b[\s\S]*?<\/(?:code|pre)>/gi,
            match => {
                const token = '\u0000S' + stash.length + '\u0000';
                stash.push(match);
                return token;
            }
        );
        return { text, restore: value => value.replace(/\u0000S(\d+)\u0000/g, (_, index) => stash[Number(index)]) };
    }

    function mobileFillins(source) {
        const math = /(\$\$[\s\S]*?\$\$|\$[^$\n]*\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\))/g;
        const blank = '<span class="mobile-fillin"></span>';
        const outside = text => text.replace(/\\fillin(?:\[[^\]]*\])?(?:\[[^\]]*\])?/g, blank);
        const inside = text => text.replace(/\\fillin(?:\[[^\]]*\])?(?:\[[^\]]*\])?/g, '\\underline{\\hspace{1.5cm}}');
        let output = '', last = 0, match;
        while ((match = math.exec(source)) !== null) {
            output += outside(source.slice(last, match.index)) + inside(match[0]);
            last = match.index + match[0].length;
        }
        return output + outside(source.slice(last));
    }

    function mobileQuestionMarkdown(source) {
        const literals = protectMobileLiterals(source);
        let text = literals.text;
        // The mobile app intentionally does not serve uploads, so images are
        // replaced with a readable note instead of broken image tags.
        text = text.replace(/!\[[^\]]*\]\([^)]*\)/g, '（配图请在电脑查看）')
            .replace(/<img\b[^>]*>/gi, '（配图请在电脑查看）');
        text = text.replace(/\\begin\{(equation|align|gather|multline)(\*?)\}([\s\S]*?)\\end\{\1\2\}/g,
            (whole, env, _star, body) => {
                const name = env === 'equation' ? '' : env === 'align' ? 'aligned' : 'gathered';
                return name ? `$$\\begin{${name}}${body}\\end{${name}}$$` : `$$${body}$$`;
            });
        text = text.replace(/\\begin\{choices\}(?:\[[^\]]*\])?([\s\S]*?)\\end\{choices\}/g, (whole, body) => {
            const items = body.split(/\\item(?![A-Za-z])/).slice(1).map(item => item.trim()).filter(Boolean);
            if (!items.length) return whole;
            return '\n\n' + items.map((item, index) =>
                `**${'ABCDEFGH'[index] || index + 1}.** ${item}`).join('\n\n') + '\n\n';
        });
        text = mobileFillins(text);
        return literals.restore(text);
    }

    function renderMobileQuestion(target, source, options = {}) {
        const text = String(source || '');
        target.replaceChildren();
        if (!text.trim()) {
            target.textContent = options.empty || '暂无内容';
            return;
        }
        try {
            target.innerHTML = window.DOMPurify.sanitize(window.marked.parse(mobileQuestionMarkdown(text)));
            window.renderMathInElement(target, {
                delimiters: [
                    { left: '$$', right: '$$', display: true },
                    { left: '$', right: '$', display: false },
                    { left: '\\(', right: '\\)', display: false },
                    { left: '\\[', right: '\\]', display: true }
                ],
                throwOnError: false,
                errorColor: '#b45309',
                ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'option'],
                macros: {
                    '\\iu': '\\mathrm{i}', '\\upi': '\\mathrm{i}',
                    '\\eu': '\\mathrm{e}', '\\upe': '\\mathrm{e}',
                    '\\degree': '^{\\circ}', '\\celsius': '^{\\circ}\\mathrm{C}'
                }
            });
        } catch (error) {
            target.textContent = text;
        }
    }

    // Compress only photos that exceed the upload size or resolution budget.
    const MAX_IMAGE_EDGE = 3000;
    function needsShrink(size, width, height, limitBytes) {
        return size > limitBytes || Math.max(width, height) > MAX_IMAGE_EDGE;
    }

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
    function action(fn) { return event => { if (event) event.preventDefault(); Promise.resolve().then(() => fn(event)).catch(error => say(friendlyError(error))); }; }

    let pairing = false;
    async function pairDevice(payload) {
        if (pairing) return;
        pairing = true;
        const button = $('pairForm').querySelector('button'); button.disabled = true;
        try {
            const data = await request('/api/pair', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
            token = data.token; sessionStorage.setItem('starhope-mobile-token', token); $('code').value = '';
            connected(true); say('已连接电脑。可以拍照传送，或查看共享题库。');
            // Upload metadata is optional; failing it must not leave us stuck on the pairing screen.
            try { limit = (await request('/api/connection')).max_image_bytes; }
            catch (error) {
                limit = limit || DEFAULT_LIMIT;
                if (!token) { connected(false); say('连接已失效，请重新扫码或输入配对码。'); }
            }
        } finally { button.disabled = false; pairing = false; }
    }
    $('pairForm').addEventListener('submit', action(() => pairDevice({code:$('code').value.trim()})));
    $('disconnect').addEventListener('click', action(async () => { if (busy) return; await request('/api/disconnect', {method:'POST', body:''}); serial++; token = ''; sessionStorage.removeItem('starhope-mobile-token'); connected(false); say('已断开此页面，电脑收件箱中的照片仍保留。'); }));
    for (const [button, target] of [['photoTab','photoPanel'], ['bankTab','bankPanel']]) {
        $(button).addEventListener('click', () => {
            for (const id of ['photoPanel','bankPanel']) $(id).hidden = id !== target;
            $('photoTab').setAttribute('aria-pressed', String(target === 'photoPanel')); $('bankTab').setAttribute('aria-pressed', String(target === 'bankPanel'));
            if (target === 'bankPanel') loadBank().catch(error => say(friendlyError(error)));
        });
    }

    async function loadImage(source) {
        if (window.createImageBitmap) {
            const bitmap = await createImageBitmap(source);
            return {node: bitmap, width: bitmap.width, height: bitmap.height, release: () => bitmap.close && bitmap.close()};
        }
        const url = URL.createObjectURL(source);
        try {
            const image = await new Promise((resolve, reject) => {
                const node = new Image();
                node.onload = () => resolve(node);
                node.onerror = () => reject(new Error('无法读取这张照片，请换一张或重拍。'));
                node.src = url;
            });
            return {node: image, width: image.naturalWidth, height: image.naturalHeight, release: () => URL.revokeObjectURL(url)};
        } catch (error) { URL.revokeObjectURL(url); throw error; }
    }

    function toBlob(canvas, quality) {
        return new Promise((resolve, reject) => canvas.toBlob(blob => blob
            ? resolve(blob) : reject(new Error('照片压缩失败，请重试。')), 'image/jpeg', quality));
    }

    async function shrink(source, limitBytes) {
        const image = await loadImage(source);
        try {
            if (!needsShrink(source.size, image.width, image.height, limitBytes)) return {file: source, note: ''};
            const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(image.width, image.height));
            const canvas = document.createElement('canvas');
            canvas.width = Math.max(1, Math.round(image.width * scale));
            canvas.height = Math.max(1, Math.round(image.height * scale));
            canvas.getContext('2d').drawImage(image.node, 0, 0, canvas.width, canvas.height);
            let blob = await toBlob(canvas, 0.9);
            for (const quality of [0.8, 0.7, 0.6]) { if (blob.size <= limitBytes) break; blob = await toBlob(canvas, quality); }
            if (blob.size > limitBytes) throw new Error('照片压缩后仍超过上传上限，请降低相机分辨率后重拍。');
            const name = (source.name || 'photo').replace(/\.[^.]+$/, '') + '.jpg';
            return {file: new File([blob], name, {type:'image/jpeg'}),
                    note: `已压缩 ${(source.size/1048576).toFixed(1)} → ${(blob.size/1048576).toFixed(1)} MiB`};
        } finally { image.release(); }
    }

    function cleared() {
        if (preview) URL.revokeObjectURL(preview);
        preview = ''; file = null; identity = '';
        $('preview').removeAttribute('src'); $('preview').hidden = true;
        $('photoInfo').textContent = ''; $('retake').hidden = true; $('send').disabled = true;
    }

    async function select(input) {
        if (busy || !input.files[0]) return;
        const next = input.files[0]; input.value = '';
        if (!next.type.startsWith('image/')) { say('请选择照片。'); return; }
        const generation = ++selectSerial;
        cleared(); $('preview').hidden = true; $('photoInfo').textContent = '正在准备照片…';
        say('正在准备照片…');
        let prepared;
        try { prepared = await shrink(next, limit || DEFAULT_LIMIT); }
        catch (error) { if (generation === selectSerial) { $('photoInfo').textContent = ''; say(friendlyError(error)); } return; }
        if (generation !== selectSerial) return;
        file = prepared.file;
        // Native randomUUID requires HTTPS on some Android browsers; HTTP LAN uses getRandomValues.
        const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
        const hex = [...bytes].map(value => value.toString(16).padStart(2,'0')).join('');
        identity = `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
        preview = URL.createObjectURL(file); $('preview').src = preview; $('preview').hidden = false;
        $('photoInfo').textContent = `${file.name} · ${(file.size/1048576).toFixed(1)} MiB` + (prepared.note ? ' · ' + prepared.note : '');
        $('retake').hidden = false; $('send').disabled = false; say('照片已选择，请确认清晰后传给电脑。');
    }
    $('camera').addEventListener('change', () => { select($('camera')).catch(error => say(friendlyError(error))); });
    $('album').addEventListener('change', () => { select($('album')).catch(error => say(friendlyError(error))); });
    $('retake').addEventListener('click', () => {
        if (busy) return;
        selectSerial++; cleared(); say('已移除照片，可以重新拍摄或选择。');
    });

    function upload(form, onProgress) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/photos?identity=' + encodeURIComponent(identity));
            if (token) xhr.setRequestHeader('Authorization', 'Bearer ' + token);
            xhr.timeout = 600000;
            xhr.upload.onprogress = event => { if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total); };
            xhr.onload = () => {
                let value = {};
                try { value = JSON.parse(xhr.responseText); } catch (_) { /* non-JSON errors keep their status */ }
                if (xhr.status === 401) { token = ''; sessionStorage.removeItem('starhope-mobile-token'); connected(false); }
                if (xhr.status >= 200 && xhr.status < 300) resolve(value);
                else reject(new Error(typeof value.detail === 'string' ? value.detail : '传送失败，请保留照片后重试。'));
            };
            xhr.onerror = () => reject(new TypeError('Network request failed'));
            xhr.ontimeout = () => reject(new Error('传送超时，请保留照片后重试。'));
            xhr.send(form);
        });
    }

    $('send').addEventListener('click', action(async () => {
        if (busy || !file) return;
        busy = true; $('send').disabled = true; $('camera').disabled = true; $('album').disabled = true; $('retake').disabled = true;
        $('uploadProgress').hidden = false; $('uploadProgress').value = 0; $('progressText').textContent = '0%';
        say('正在传送，请保持本页开启…');
        try {
            const form = new FormData(); form.append('file', file);
            await upload(form, ratio => {
                $('uploadProgress').value = ratio; $('progressText').textContent = Math.round(ratio*100) + '%';
                if (ratio >= 1) say('已发送，电脑正在保存照片…');
            });
            const row = document.createElement('li'); row.textContent = '电脑已接收 · ' + new Date().toLocaleTimeString() + ' · ' + file.name; $('receipts').prepend(row);
            cleared();
            say('电脑已接收并保存照片，请在电脑「手机互联」中校对录入。可以继续拍下一题。');
        } finally {
            busy = false; $('camera').disabled = false; $('album').disabled = false; $('retake').disabled = false;
            $('uploadProgress').hidden = true; $('send').disabled = !file;
        }
    }));

    async function loadBank() {
        const generation = ++serial;
        const result = await request('/api/library?q=' + encodeURIComponent($('query').value.trim()) + '&page=' + page);
        if (generation !== serial || !token) return;
        $('questions').replaceChildren();
        for (const item of result.items) {
            const article = document.createElement('article'), title = document.createElement('h3');
            const body = document.createElement('div'), detail = document.createElement('details');
            const summary = document.createElement('summary'), answer = document.createElement('div');
            const source = document.createElement('p');
            title.textContent = `#${item.id} · ${item.education_stage || '未标注'} · ${item.subject || '数学'}`;
            body.className = 'mobile-question-body';
            renderMobileQuestion(body, item.content);
            summary.textContent = '查看答案与来源';
            answer.className = 'mobile-answer-body';
            source.className = 'mobile-source';
            source.textContent = item.source ? '来源：' + item.source : '未标注来源';
            let answerRendered = false;
            detail.addEventListener('toggle', () => {
                if (!detail.open || answerRendered) return;
                answerRendered = true;
                renderMobileQuestion(answer, item.answer_markdown, { empty: '暂无答案' });
            });
            detail.append(summary, answer, source);
            article.append(title, body, detail);
            $('questions').append(article);
        }
        $('bankSummary').textContent = `共 ${result.total} 题 · 第 ${page} 页 · 题文与答案已渲染`;
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
        pairDevice({ticket}).catch(error => { say(error && error.name === 'AbortError' ? '扫码连接超时，请在电脑更换二维码后重试，或手动配对。' : friendlyError(error)); $('code').focus(); });
    }
    // Scanners that reuse an open tab may not navigate; re-check on focus and bfcache restore.
    function scanTicket() { const ticket = takeQrTicket(); if (ticket) connectQr(ticket); }
    window.addEventListener('hashchange', scanTicket);
    window.addEventListener('pageshow', scanTicket);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) scanTicket(); });
    if (qrTicket) connectQr(qrTicket);
    else if (token) request('/api/connection').then(data => { limit = data.max_image_bytes || DEFAULT_LIMIT; connected(true); say('已恢复与电脑的连接。'); }).catch(error => {
        say(error instanceof TypeError
            ? '连不上电脑：请确认电脑题库已开启手机互联，且手机与电脑在同一 Wi-Fi。'
            : friendlyError(error));
        connected(false);
    });
})();
