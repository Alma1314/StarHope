(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    let timer, pending = false, opened = false, serial = 0, epoch = 0, snapshot = '';
    let connectionData = null, addressSnapshot = '', qrKey = '', qrObjectUrl = '', qrSerial = 0;
    const thumbUrls = new Map(), fullUrls = new Map();
    const message = text => { $('mobileLinkMessage').textContent = text; };
    async function request(path = '', method = 'GET') {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), window.StarHopeTimeouts ? StarHopeTimeouts.QUERY_MS : 60000);
        try {
            const response = await fetch('/api/mobile-link' + path, {method, signal:controller.signal, headers:{'X-Local-Token':window.__localToken || localStorage.getItem('local_token') || ''}});
            if (!response.ok) { const body = await response.json(); throw new Error(typeof body.detail === 'string' ? body.detail : '互联操作失败，请稍后重试。'); }
            return response;
        } finally { clearTimeout(timeout); }
    }
    function action(fn) { return () => {
        if (pending) return;
        pending = true;
        for (const button of $('mobileLinkModal').querySelectorAll('button:not([data-modal-close])')) button.disabled = true;
        Promise.resolve().then(fn).catch(error => message(error.name === 'AbortError' ? '连接超时，请刷新状态后重试。' : error.message)).finally(() => {
            pending = false;
            for (const button of $('mobileLinkModal').querySelectorAll('button')) button.disabled = false;
        });
    }; }
    function close() { opened = false; serial++; epoch++; clearTimeout(timer); clearQr(); StarHopeModal.close($('mobileLinkModal')); $('mobileLinkModal').classList.add('hidden'); }
    function textNode(tag, text, className) { const element = document.createElement(tag); element.textContent = text; if (className) element.className = className; return element; }
    async function photo(row) {
        const response = await request('/photos/' + row.id);
        return new File([await response.blob()], 'phone-' + row.id + row.extension, {type:row.media_type});
    }
    function thumbnailUrl(row) {
        if (thumbUrls.has(row.id)) return Promise.resolve(thumbUrls.get(row.id));
        return request('/photos/' + row.id + '/thumb').then(response => response.blob()).then(blob => {
            const url = URL.createObjectURL(blob); thumbUrls.set(row.id, url);
            return url;
        });
    }
    function forgetPhotos(rows) {
        const alive = new Set(rows.map(row => row.id));
        for (const [id, url] of thumbUrls) if (!alive.has(id)) { URL.revokeObjectURL(url); thumbUrls.delete(id); }
        for (const [id, url] of fullUrls) if (!alive.has(id)) { URL.revokeObjectURL(url); fullUrls.delete(id); }
    }
    function clearQr() {
        qrSerial++; qrKey = '';
        if (qrObjectUrl) URL.revokeObjectURL(qrObjectUrl);
        qrObjectUrl = ''; $('mobileLinkQr').removeAttribute('src'); $('mobileLinkQr').classList.add('hidden');
    }
    async function drawQr(data) {
        const select = $('mobileLinkAddress');
        if (!data.enabled || !data.qr_revision || !select.value) {
            clearQr();
            $('mobileLinkQrStatus').textContent = !data.enabled ? '' : !select.value ? '未发现局域网地址，请先检查网络连接。' : '二维码已过期，点击“更换二维码 / 配对码”生成新码。';
            return;
        }
        const key = data.qr_revision + '|' + select.value;
        if (key === qrKey) return;
        clearQr(); qrKey = key; const generation = qrSerial;
        $('mobileLinkQrStatus').textContent = '正在生成连接二维码…';
        try {
            const address = new URL(select.value).hostname;
            const response = await request('/qr?address=' + encodeURIComponent(address));
            const blob = await response.blob();
            if (!opened || generation !== qrSerial) return;
            qrObjectUrl = URL.createObjectURL(blob); $('mobileLinkQr').src = qrObjectUrl; $('mobileLinkQr').classList.remove('hidden');
            $('mobileLinkQrStatus').textContent = '扫码即可连接 · 十分钟内有效 · 已连接的手机不受影响';
        } catch (error) {
            if (opened && generation === qrSerial) { qrKey = ''; $('mobileLinkQrStatus').textContent = error.message; }
        }
    }
    function render(data) {
        $('mobileLinkStart').hidden = data.enabled; $('mobileLinkStop').hidden = !data.enabled; $('mobileLinkCode').hidden = !data.enabled;
        $('mobileLinkConnection').hidden = !data.enabled;
        $('mobileLinkPin').textContent = data.pairing_code || '已过期，请更换';
        $('mobileLinkDevices').textContent = `已配对 ${data.devices} 台 · 配对码剩余 ${data.code_seconds} 秒`;
        connectionData = data;
        const addresses = JSON.stringify(data.urls);
        if (addresses !== addressSnapshot) {
            addressSnapshot = addresses;
            const select = $('mobileLinkAddress'), previous = select.value;
            select.replaceChildren();
            for (const url of data.urls) { const option = textNode('option',url); option.value=url; select.append(option); }
            if (data.urls.includes(previous)) select.value=previous;
        }
        drawQr(data);
        $('mobileLinkUrls').replaceChildren();
        for (const url of data.urls) { $('mobileLinkUrls').append(textNode('p',url,'font-mono select-all')); }
        if (data.enabled && !data.urls.length) $('mobileLinkUrls').append(textNode('p',`尚未找到局域网 IPv4 地址，可用 http://电脑局域网IP:${data.port} 连接。`));
        const next = JSON.stringify(data.photos);
        if (snapshot === next) return;
        snapshot = next; forgetPhotos(data.photos); $('mobileLinkInbox').replaceChildren();
        if (!data.photos.length) $('mobileLinkInbox').append(textNode('p','暂无手机照片，开启互联后可从安卓手机拍照传送。','text-sm text-slate-500 py-3'));
        data.photos.forEach((row,index) => {
            const card = document.createElement('div'); card.className = 'border border-slate-200 rounded-xl p-3 my-2 flex gap-3 items-start';
            const image = document.createElement('img');
            image.alt = `照片 ${index+1} 缩略图`; image.className = 'w-24 h-24 rounded-lg object-cover bg-slate-100 shrink-0 cursor-zoom-in';
            thumbnailUrl(row).then(url => { image.src = url; }).catch(() => {});
            const body = document.createElement('div'); body.className = 'flex-1 min-w-0';
            body.append(textNode('p',`照片 ${index+1} · ${row.width} × ${row.height} · ${new Date(row.created_at*1000).toLocaleString()}`,'text-sm mb-2'));
            const full = document.createElement('img');
            full.alt = `照片 ${index+1} 原图`; full.className = 'hidden w-full rounded-lg mt-2 bg-slate-100';
            image.addEventListener('click', async () => {
                if (!full.src) {
                    try { full.src = URL.createObjectURL(await photo(row)); fullUrls.set(row.id, full.src); }
                    catch (error) { message(error.message); return; }
                }
                full.classList.toggle('hidden');
            });
            const actions = document.createElement('div'); actions.className = 'flex flex-wrap gap-2';
            const button = (label,handler) => { const value = textNode('button',label,'glass-btn px-3 py-2 rounded-lg text-sm'); value.type='button'; value.addEventListener('click',action(handler)); actions.append(value); };
            button('下载照片',async () => {
                const file = await photo(row), url = URL.createObjectURL(file), link = document.createElement('a');
                link.href=url; link.download=file.name; link.click(); setTimeout(()=>URL.revokeObjectURL(url),60000);
                message('照片已下载，可在本地预览。');
            });
            button('识别为新题',async () => {
                const file = await photo(row);
                close();
                // Existing editor guard handles unsaved edits before OCR is started.
                openNewQuestionEditor(() => runContentOcr(file));
            });
            button('用于已做题扫描',async () => {
                const file = await photo(row);
                selectWorkspace('learning','学习档案');
                if (LearningWorkspace.receivePhoto(file)) close();
            });
            button('移除照片',async () => {
                if (!confirm('确认从电脑收件箱移除此照片？请先确认已完成校对或下载。')) return;
                await request('/photos/'+row.id,'DELETE'); snapshot=''; await refresh(); message('已移除收件箱照片。');
            });
            body.append(actions, full); card.append(image, body); $('mobileLinkInbox').append(card);
        });
    }
    async function refresh() {
        const generation = ++serial;
        const data = await (await request()).json();
        if (opened && generation === serial) render(data);
    }
    async function poll(generation) {
        try { if (opened && !pending) await refresh(); }
        catch (error) { if (opened && generation === epoch) message('暂时无法读取互联状态：'+error.message); }
        finally { if (opened && generation === epoch) timer=setTimeout(() => poll(generation),4000); }
    }
    $('mobileLinkOpen').addEventListener('click', () => {
        if (opened) return;
        opened=true; snapshot=''; message(''); $('mobileLinkModal').classList.remove('hidden'); StarHopeModal.open($('mobileLinkModal'),{onEscape:close}); poll(++epoch);
    });
    $('mobileLinkClose').addEventListener('click',close);
    $('mobileLinkAddress').addEventListener('change', () => { if (connectionData) drawQr(connectionData); });
    $('mobileLinkStart').addEventListener('click',action(async () => { await request('/start','POST'); await refresh(); message('已开启。请用安卓手机扫码，打开后会自动连接。'); }));
    $('mobileLinkStop').addEventListener('click',action(async () => { await request('/stop','POST'); await refresh(); message('已断开全部手机，收件箱照片仍保留。'); }));
    $('mobileLinkCode').addEventListener('click',action(async () => { await request('/code','POST'); await refresh(); message('二维码和配对码已更换；已连接设备不受影响。'); }));
    $('mobileLinkCopy').addEventListener('click',action(async () => {
        const value = $('mobileLinkAddress').value;
        if (!value) { message('暂无可复制的连接地址。'); return; }
        try { await navigator.clipboard.writeText(value); message('连接地址已复制，可在手机浏览器中手动打开。'); }
        catch (_) { message('复制失败，请选中上方地址手动复制。'); }
    }));
})();
