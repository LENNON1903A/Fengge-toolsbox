/* ==================== 签章工具 sign-stamp ==================== */
const SignStampTool = (function () {
  const MM = 2.834645669;                 // 1mm -> pt
  const DIA_DEFAULT = 40, ANG_DEFAULT = 0, SIGNW_DEFAULT = 30;

  // 长词优先：投保人签章处是"签字"，签章才是"盖章"
  const KEYS = [
    { k: '投保人签章处', kind: 'sign' }, { k: '投保人签章', kind: 'sign' },
    { k: '法定代表人签章', kind: 'sign' }, { k: '法定代表人（签字）', kind: 'sign' },
    { k: '授权代表签字', kind: 'sign' }, { k: '委托代理人签字', kind: 'sign' },
    { k: '签字处', kind: 'sign' }, { k: '签名处', kind: 'sign' },
    { k: '签章处', kind: 'sign' }, { k: '盖章处', kind: 'seal' },
    { k: '签字', kind: 'sign' }, { k: '签名', kind: 'sign' },
    { k: '法定代表人', kind: 'sign' }, { k: '委托代理人', kind: 'sign' },
    { k: '授权代表', kind: 'sign' }, { k: '经办人', kind: 'sign' },
    { k: '负责人', kind: 'sign' }, { k: '盖章', kind: 'seal' },
    { k: '公章', kind: 'seal' }, { k: '签章', kind: 'seal' },
    { k: '印章', kind: 'seal' }, { k: '（章）', kind: 'seal' }, { k: '(章)', kind: 'seal' }
  ];

  let S = null, keyAc = null;

  const $ = id => document.getElementById(id);
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

  function log(cls, msg) {
    const box = $('stLog'); if (!box) return;
    const row = el('div');
    const tag = cls === 'ok' ? '✓ ' : cls === 'warn' ? '⚠ ' : '· ';
    row.className = cls || '';
    row.textContent = tag + msg;
    box.appendChild(row);
    while (box.children.length > 30) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
  }

  function busy(on, txt) {
    const btn = $('stGo');
    if (btn) {
      btn.dataset.busy = on ? '1' : '';
      btn.disabled = !!on;
      btn.textContent = on ? (txt || '处理中…') : '⬇ 生成 PDF';
    }
  }

  /** 会吞掉 Delete 的只有真正的文本输入控件。 */
  function isTextEntry(t) {
    if (!t) return false;
    const tag = t.tagName || '';
    if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
    if (t.isContentEditable) return true;
    if (tag !== 'INPUT') return false;
    const type = (t.type || 'text').toLowerCase();
    return ['text', 'search', 'email', 'number', 'password', 'tel', 'url',
      'date', 'datetime-local', 'month', 'week', 'time'].indexOf(type) >= 0;
  }

  /* ---------- 图片 ---------- */
  function loadImage(url) {
    return new Promise((res, rej) => {
      const im = new Image();
      im.onload = () => res(im);
      im.onerror = () => rej(new Error('图片读不出来'));
      im.src = url;
    });
  }

  /**
   * 裁掉透明边，让"公章直径"真的是红环外径而不是画布宽度。
   * 检测在缩略图上做（最多 800px），避免 4000px 大图卡住主线程。
   */
  async function trimImage(file) {
    const url = URL.createObjectURL(file);
    let img;
    try { img = await loadImage(url); } finally { /* url 稍后 revoke */ }

    const W = img.naturalWidth, H = img.naturalHeight;
    const probe = Math.min(1, 800 / Math.max(W, H));
    const pw = Math.max(1, Math.round(W * probe)), ph = Math.max(1, Math.round(H * probe));
    const tmp = document.createElement('canvas');
    tmp.width = pw; tmp.height = ph;
    const tg = tmp.getContext('2d', { willReadFrequently: true });
    tg.drawImage(img, 0, 0, pw, ph);
    const d = tg.getImageData(0, 0, pw, ph).data;

    let x0 = pw, y0 = ph, x1 = -1, y1 = -1;
    for (let y = 0; y < ph; y++) {
      const row = y * pw;
      for (let x = 0; x < pw; x++) {
        if (d[(row + x) * 4 + 3] > 10) {
          if (x < x0) x0 = x; if (x > x1) x1 = x;
          if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
      }
    }

    const out = document.createElement('canvas');
    let sx = 0, sy = 0, sw = W, sh = H;
    if (x1 >= 0 && (x1 - x0 + 1) < pw * 0.999) {
      sx = Math.max(0, Math.floor(x0 / probe) - 1);
      sy = Math.max(0, Math.floor(y0 / probe) - 1);
      sw = Math.min(W - sx, Math.ceil((x1 - x0 + 1) / probe) + 2);
      sh = Math.min(H - sy, Math.ceil((y1 - y0 + 1) / probe) + 2);
    }
    out.width = sw; out.height = sh;
    out.getContext('2d').drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
    URL.revokeObjectURL(url);

    const blob = await new Promise(r => out.toBlob(r, 'image/png'));
    const bmpUrl = URL.createObjectURL(blob);
    return { blob: blob, url: bmpUrl, w: sw, h: sh };
  }

  /* ---------- 结构 ---------- */
  const TPL = `
<div class="st-app">
  <div class="st-topbar">
    <div class="tip" id="stTip"><b>上传 PDF → 上传公章/签字 → 点页面放置</b>　文件只在本机浏览器里处理，不会上传</div>
    <div class="st-acts">
      <button class="st-btn ghost" id="stAuto">⚡ 自动找位置</button>
      <button class="st-btn green" id="stGo">⬇ 生成 PDF</button>
    </div>
  </div>

  <div class="st-col">
    <div class="st-card">
      <h3>① 素材</h3>
      <div class="st-file" id="stPickPdf">
        <div class="thumb">📄</div>
        <div class="txt"><b id="stPdfName">选择 PDF</b><span id="stPdfInfo">点一下选文件</span></div>
      </div>
      <div class="st-file" id="stPickSeal">
        <div class="thumb" id="stSealThumb">🔴</div>
        <div class="txt"><b id="stSealName">上传公章</b><span>PNG 透明底最好</span></div>
      </div>
      <div class="st-file" id="stPickSign">
        <div class="thumb" id="stSignThumb">✍️</div>
        <div class="txt"><b id="stSignName">上传签字</b><span>手写签名 PNG</span></div>
      </div>
    </div>

    <div class="st-card">
      <h3>② 尺寸与角度</h3>
      <div class="st-params">
        <div class="st-p">
          <div class="hd"><span>公章直径</span><em id="stDiaVal">40 mm</em><button class="st-reset" id="stDiaReset" title="复位到 40 mm">↺</button></div>
          <input type="range" id="stDia" min="20" max="60" step="1" value="40">
        </div>
        <div class="st-p">
          <div class="hd"><span>旋转角度</span><em id="stAngVal">0°</em><button class="st-reset" id="stAngReset" title="复位到 0°">↺</button></div>
          <input type="range" id="stAng" min="-90" max="90" step="1" value="0">
        </div>
        <div class="st-p">
          <div class="hd"><span>签字宽度</span><em id="stSignWVal">30 mm</em><button class="st-reset" id="stSignWReset" title="复位到 30 mm">↺</button></div>
          <input type="range" id="stSignW" min="15" max="70" step="1" value="30">
        </div>
      </div>
    </div>

    <div class="st-card">
      <h3>③ 放置</h3>
      <button class="st-btn ghost" id="stArmSeal">＋ 点页面放公章</button>
      <button class="st-btn ghost" id="stArmSign">＋ 点页面放签字</button>
      <button class="st-btn ghost" id="stDel" disabled>✕ 删除选中（Delete）</button>
    </div>
  </div>

  <div class="st-stage-wrap">
    <div class="st-stage" id="stStage">
      <div class="st-empty" id="stEmpty">
        <div class="big">🔏</div>
        <b>先选一个 PDF</b>
        <div>点左边「选择 PDF」，然后上传公章或签字</div>
      </div>
      <div class="st-page" id="stPage" style="display:none">
        <canvas id="stCanvas"></canvas>
        <div class="st-overlay" id="stOverlay"></div>
      </div>
    </div>
    <div class="st-thumbs" id="stThumbs"></div>
    <div class="st-log" id="stLog"></div>
  </div>
</div>

<input type="file" id="stFilePdf" accept=".pdf,application/pdf" style="display:none">
<input type="file" id="stFileSeal" accept="image/*" style="display:none">
<input type="file" id="stFileSign" accept="image/*" style="display:none">
`;

  /* ---------- 渲染 ---------- */
  let renderTask = null;

  async function goPage(n) {
    if (!S || !S.pdf) return;
    S.cur = n;
    S.sel = null;
    const page = await S.pdf.getPage(n);
    const base = page.getViewport({ scale: 1 });
    const stage = $('stStage');
    const avail = Math.max(240, stage.clientWidth - 30);
    S.scale = Math.min(avail / base.width, 2.4) * (S.zoom || 1);
    S.vp = page.getViewport({ scale: S.scale });

    const cv = $('stCanvas');
    cv.width = Math.round(S.vp.width);
    cv.height = Math.round(S.vp.height);
    cv.style.width = Math.round(S.vp.width) + 'px';
    cv.style.height = Math.round(S.vp.height) + 'px';

    // 先把画布亮出来再渲染：晚一步的话用户会看到"先选一个 PDF"的提示闪一下
    $('stPage').style.display = '';
    $('stEmpty').style.display = 'none';

    if (renderTask) { try { renderTask.cancel(); } catch (e) { } renderTask = null; }
    renderTask = page.render({ canvasContext: cv.getContext('2d'), viewport: S.vp });
    try { await renderTask.promise; } catch (e) { if (e && e.name !== 'RenderingCancelledException') throw e; }
    renderTask = null;

    stage.scrollTop = 0;
    renderObjs();
    paintThumbActive();
    syncUI();
  }

  function thumbUrlFor(kind) {
    if (kind === 'seal') return S.seal ? S.seal.url : '';
    return S.sign ? S.sign.url : '';
  }

  function renderObjs() {
    const ov = $('stOverlay');
    if (!ov || !S.vp) return;
    ov.innerHTML = '';
    const arr = S.objs.get(S.cur) || [];
    for (const o of arr) {
      const d = el('div', 'st-obj ' + o.kind + (S.sel === o ? ' sel' : ''));
      d.dataset.id = String(o.id);
      const p = S.vp.convertToViewportPoint(o.cx, o.cy);
      const w = o.w * S.scale, h = o.h * S.scale;
      d.style.left = (p[0] - w / 2) + 'px';
      d.style.top = (p[1] - h / 2) + 'px';
      d.style.width = w + 'px';
      d.style.height = h + 'px';
      d.style.transform = 'rotate(' + o.rot + 'deg)';
      const im = el('img');
      im.src = thumbUrlFor(o.kind);
      im.alt = o.kind === 'seal' ? '公章' : '签字';
      d.appendChild(im);
      ov.appendChild(d);
    }
  }

  /** 把某个对象滚到可视区中间（页面通常比画布区高）。 */
  function scrollToObj(o) {
    const stage = $('stStage'), page = $('stPage');
    if (!stage || !page || !S.vp) return;
    const p = S.vp.convertToViewportPoint(o.cx, o.cy);
    stage.scrollTop = Math.max(0, page.offsetTop + p[1] - stage.clientHeight / 2);
  }

  function paintThumbActive() {    const box = $('stThumbs'); if (!box) return;
    [...box.children].forEach(c => c.classList.toggle('on', Number(c.dataset.page) === S.cur));
    const on = box.querySelector('.st-thumb.on');
    if (on) on.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  async function buildThumbs() {
    const box = $('stThumbs');
    box.innerHTML = '';
    for (let p = 1; p <= S.pages; p++) {
      const b = el('button', 'st-thumb');
      b.dataset.page = String(p);
      b.onclick = () => goPage(p);
      const cv = document.createElement('canvas');
      b.appendChild(cv);
      b.appendChild(el('b', null, String(p)));
      box.appendChild(b);
    }
    // 缩略图逐张画，先让界面出来
    for (let p = 1; p <= S.pages; p++) {
      const card = box.children[p - 1];
      if (!card) continue;
      const page = await S.pdf.getPage(p);
      const sc = 0.09;
      const vp = page.getViewport({ scale: sc });
      const cv = card.querySelector('canvas');
      cv.width = Math.round(vp.width);
      cv.height = Math.round(vp.height);
      try { await page.render({ canvasContext: cv.getContext('2d'), viewport: vp }).promise; } catch (e) { }
    }
    paintThumbActive();
  }

  function markDots() {
    const box = $('stThumbs'); if (!box) return;
    [...box.children].forEach(c => {
      const p = Number(c.dataset.page);
      const has = (S.objs.get(p) || []).length > 0;
      let dot = c.querySelector('.dot');
      if (has && !dot) c.appendChild(el('span', 'dot'));
      if (!has && dot) dot.remove();
    });
  }

  /* ---------- 对象 ---------- */
  function sizeFor(kind) {
    if (kind === 'seal') {
      const w = S.dia * MM;
      return { w: w, h: w * (S.seal.h / S.seal.w) };
    }
    const w = S.signW * MM;
    return { w: w, h: w * (S.sign.h / S.sign.w) };
  }

  function addObj(kind, cx, cy) {
    const src = kind === 'seal' ? S.seal : S.sign;
    if (!src) { log('warn', '先上传' + (kind === 'seal' ? '公章' : '签字') + '图片'); return null; }
    const sz = sizeFor(kind);
    const o = { id: ++S.seq, kind: kind, cx: cx, cy: cy, w: sz.w, h: sz.h, rot: kind === 'seal' ? S.ang : 0 };
    if (!S.objs.has(S.cur)) S.objs.set(S.cur, []);
    S.objs.get(S.cur).push(o);
    S.sel = o;
    renderObjs();
    markDots();
    syncUI();
    return o;
  }

  function deleteSel() {
    if (!S || !S.sel) return;
    const arr = S.objs.get(S.cur) || [];
    const i = arr.indexOf(S.sel);
    if (i >= 0) arr.splice(i, 1);
    if (!arr.length) S.objs.delete(S.cur);
    log('ok', '已删除这个' + (S.sel.kind === 'seal' ? '公章' : '签字'));
    S.sel = null;
    renderObjs();
    markDots();
    syncUI();
  }

  function applySize() {
    for (const arr of S.objs.values()) {
      for (const o of arr) {
        const sz = sizeFor(o.kind);
        o.w = sz.w; o.h = sz.h;
        if (o.kind === 'seal') o.rot = S.ang;
      }
    }
    renderObjs();
  }

  /* ---------- 交互 ---------- */
  function bindStage() {
    const ov = $('stOverlay');
    const stage = $('stStage');
    let drag = null;

    ov.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      const hit = e.target.closest('.st-obj');
      if (hit) {
        const id = Number(hit.dataset.id);
        const o = (S.objs.get(S.cur) || []).find(x => x.id === id);
        if (!o) return;
        S.sel = o;
        renderObjs();
        syncUI();
        drag = { o: o, x: e.clientX, y: e.clientY, cx: o.cx, cy: o.cy };
        hit.setPointerCapture && hit.setPointerCapture(e.pointerId);
        e.preventDefault();
        return;
      }
      if (S.armed) {
        const r = ov.getBoundingClientRect();
        const px = e.clientX - r.left, py = e.clientY - r.top;
        const p = S.vp.convertToPdfPoint(px, py);
        addObj(S.armed, p[0], p[1]);
        e.preventDefault();
        return;
      }
      if (S.sel) { S.sel = null; renderObjs(); syncUI(); }
    });

    window.addEventListener('pointermove', e => {
      if (!drag) return;
      const dx = (e.clientX - drag.x) / S.scale;
      const dy = (e.clientY - drag.y) / S.scale;
      drag.o.cx = drag.cx + dx;
      drag.o.cy = drag.cy - dy;          // 屏幕 y 向下，PDF y 向上
      renderObjs();
    }, { passive: true });

    window.addEventListener('pointerup', () => { if (drag) { drag = null; markDots(); } }, { passive: true });

    // Ctrl+滚轮只缩放页面内容
    stage.addEventListener('wheel', e => {
      if (!e.ctrlKey || !S.pdf) return;
      e.preventDefault();
      const dir = e.deltaY > 0 ? -1 : 1;
      S.zoom = Math.max(0.4, Math.min(3, (S.zoom || 1) + dir * 0.12));
      const t = (S.objs.get(S.cur) || []).find(x => x === S.sel);
      goPage(S.cur).then(() => { if (t) { S.sel = t; renderObjs(); } });
    }, { passive: false });
  }

  function syncUI() {
    const has = !!(S && S.sel);
    const del = $('stDel'); if (del) del.disabled = !has;
    const a1 = $('stArmSeal'), a2 = $('stArmSign');
    if (a1) a1.classList.toggle('armed', S && S.armed === 'seal');
    if (a2) a2.classList.toggle('armed', S && S.armed === 'sign');
    const st = $('stStage'); if (st) st.classList.toggle('armed', !!(S && S.armed));
    const go = $('stGo');
    if (go && !go.dataset.busy) {
      const n = S ? [...S.objs.values()].reduce((a, b) => a + b.length, 0) : 0;
      go.disabled = !S || !S.file || n === 0;
    }
  }

  function syncReset() {
    const d = $('stDiaReset'), a = $('stAngReset'), w = $('stSignWReset');
    if (!S) return;
    if (d) d.disabled = S.dia === DIA_DEFAULT;
    if (a) a.disabled = S.ang === ANG_DEFAULT;
    if (w) w.disabled = S.signW === SIGNW_DEFAULT;
  }

  function setDia(v) {
    S.dia = Math.max(20, Math.min(60, Math.round(v)));
    $('stDia').value = S.dia;
    $('stDiaVal').textContent = S.dia + ' mm';
    applySize();
    syncReset();
  }
  function setAng(v) {
    let deg = Math.max(-90, Math.min(90, Math.round(v)));
    if (Math.abs(deg) <= 1) deg = 0;
    S.ang = deg;
    $('stAng').value = deg;
    $('stAngVal').textContent = deg + '°';
    applySize();
    syncReset();
  }
  function setSignW(v) {
    S.signW = Math.max(15, Math.min(70, Math.round(v)));
    $('stSignW').value = S.signW;
    $('stSignWVal').textContent = S.signW + ' mm';
    applySize();
    syncReset();
  }

  /* ---------- 自动定位 ---------- */
  function matchKind(text) {
    const t = text.replace(/\s+/g, '');
    for (const r of KEYS) if (t.indexOf(r.k) >= 0) return r.kind;
    return null;
  }

  async function autoLocate() {
    if (!S.pdf) return log('warn', '先选 PDF');
    if (!S.seal && !S.sign) return log('warn', '先上传公章或签字');
    log('', '正在扫描文字…');
    const found = { seal: null, sign: null };
    for (let p = 1; p <= S.pages; p++) {
      const page = await S.pdf.getPage(p);
      const vp1 = page.getViewport({ scale: 1 });
      let tc;
      try { tc = await page.getTextContent(); } catch (e) { continue; }
      for (const it of tc.items) {
        if (!it.str || !it.str.trim()) continue;
        const kind = matchKind(it.str);
        if (!kind) continue;
        if (kind === 'seal' && !S.seal) continue;
        if (kind === 'sign' && !S.sign) continue;
        const m = pdfjsLib.Util.transform(vp1.transform, it.transform);
        const x0 = m[4], y0 = m[5];
        // m[0..1] 是文本方向的向量，长度等于字号倍数；必须归一化后再乘宽度，
        // 否则宽度会被放大好几倍（表现为签字被推到页面外）
        const len = Math.hypot(m[0], m[1]) || 1;
        const x1 = x0 + it.width * (m[0] / len);
        const y1 = y0 + it.width * (m[1] / len);
        const a = vp1.convertToPdfPoint(x0, y0);
        const b = vp1.convertToPdfPoint(x1, y1);
        const cx = (a[0] + b[0]) / 2, cy = (a[1] + b[1]) / 2;
        // 同一个种类只保留最靠后的那一处 —— 合同签章页通常在文档末尾
        if (!found[kind] || p > found[kind].page) {
          found[kind] = {
            page: p, cx: cx, cy: cy,
            x0: Math.min(a[0], b[0]), x1: Math.max(a[0], b[0]),
            pageW: vp1.width, text: it.str.trim().slice(0, 18)
          };
        }
      }
    }

    // 找到的按命中位置放；没找到的那种放到同一个"落点页"右下角，
    // 否则只上传了两种、只放上一种，用起来会以为没生效
    const anchor = (found.seal && found.sign) ? Math.max(found.seal.page, found.sign.page)
      : (found.seal ? found.seal.page : (found.sign ? found.sign.page : S.pages));

    const placed = [], missed = [];
    let landPage = anchor;

    for (const kind of ['seal', 'sign']) {
      const src = kind === 'seal' ? S.seal : S.sign;
      if (!src) continue;
      const f = found[kind];
      const pageNo = f ? f.page : anchor;
      const sz = sizeFor(kind);
      if (!S.objs.has(pageNo)) S.objs.set(pageNo, []);
      if (S.objs.get(pageNo).some(o => o.kind === kind)) continue;   // 该页已有同类就不重复放

      let cx, cy;
      if (f) {
        if (kind === 'sign') {
          // 手签一般写在"签字处："右边那条横线上；右边放不下才退到下方
          if (f.x1 + sz.w + 10 < f.pageW - 20) {
            cx = f.x1 + sz.w / 2 + 8;
            cy = f.cy - 3;                       // 略低于行中心，贴在横线上更自然
          } else {
            cx = (f.x0 + f.x1) / 2;
            cy = f.cy - sz.h / 2 - 8;
          }
        } else {
          cx = f.cx; cy = f.cy;                 // 公章压在字样上
        }
      } else {
        const pg = await S.pdf.getPage(pageNo);
        const vp = pg.getViewport({ scale: 1 });
        cx = vp.width - sz.w / 2 - 70;
        cy = sz.h / 2 + (kind === 'sign' ? 170 : 70);                // 末页右下角
      }
      S.objs.get(pageNo).push({
        id: ++S.seq, kind: kind, cx: cx, cy: cy, w: sz.w, h: sz.h,
        rot: kind === 'seal' ? S.ang : 0
      });
      (f ? placed : missed).push({ kind: kind, page: pageNo, text: f ? f.text : '' });
      if (pageNo > landPage) landPage = pageNo;
    }

    await goPage(landPage);
    markDots();
    syncUI();
    // 页面比可视区高，自动定位完不滚过去的话，用户根本看不到章放哪了
    const landed = S.objs.get(landPage) || [];
    if (landed.length) scrollToObj(landed[landed.length - 1]);
    placed.forEach(x => log('ok', '第 ' + x.page + ' 页「' + x.text + '」→ 放上' + (x.kind === 'seal' ? '公章' : '签字')));
    missed.forEach(x => log('warn', '没找到' + (x.kind === 'seal' ? '「盖章」' : '「签字」') +
      '字样，先放在第 ' + x.page + ' 页右下角，请拖到正确位置'));
    if (placed.length || missed.length) {
      log('', '位置不满意可以直接拖着改，选中后按 Delete 可删除');
    } else {
      log('warn', '这一页已经有公章和签字了，没有重复添加');
    }
  }

  /* ---------- 导出 ---------- */
  async function exportPdf() {
    if (!S.file) return log('warn', '先选 PDF');
    const total = [...S.objs.values()].reduce((a, b) => a + b.length, 0);
    if (!total) return log('warn', '还没放任何公章或签字');

    busy(true, '生成中…');
    try {
      const buf = await S.file.arrayBuffer();
      const doc = await PDFDocument.load(buf);
      const eSeal = S.seal ? await doc.embedPng(await S.seal.blob.arrayBuffer()) : null;
      const eSign = S.sign ? await doc.embedPng(await S.sign.blob.arrayBuffer()) : null;

      const pages = [...S.objs.keys()].sort((a, b) => a - b);
      for (const p of pages) {
        if (p < 1 || p > doc.getPageCount()) continue;
        const page = doc.getPage(p - 1);
        for (const o of S.objs.get(p)) {
          const img = o.kind === 'seal' ? eSeal : eSign;
          if (!img) continue;
          // pdf-lib 的 rotate 绕 (x,y) 在 PDF 坐标系里逆时针转，而 PDF 的 y 轴朝上、
          // 屏幕朝下，所以"屏幕顺时针 rot"在 PDF 里要写成 +rot；同时把绘制原点挪到
          // 等价的左下角，才能让图绕自身中心转而不是绕左下角转。
          const rad = o.rot * Math.PI / 180;
          const c = Math.cos(rad), s2 = Math.sin(rad);
          const hx = o.w / 2, hy = o.h / 2;
          const x = o.cx - (c * hx - s2 * hy);
          const y = o.cy - (s2 * hx + c * hy);
          page.drawImage(img, { x: x, y: y, width: o.w, height: o.h, rotate: PDFLib.degrees(o.rot) });
        }
      }
      const bytes = await doc.save();
      const name = S.file.name.replace(/\.pdf$/i, '') + '_盖章.pdf';
      showResult({ data: new Blob([bytes], { type: 'application/pdf' }), name: name, type: 'application/pdf' });
      log('ok', '生成完成，共 ' + total + ' 处，点下面的绿色按钮下载');
    } catch (e) {
      log('warn', '生成失败：' + (e && e.message ? e.message : e));
      showError(e && e.message ? e.message : String(e));
    } finally {
      busy(false);
      syncUI();
    }
  }

  /* ---------- 挂载 ---------- */
  function mount(container) {
    if (keyAc) { keyAc.abort(); keyAc = null; }
    S = {
      file: null, pdf: null, pages: 0, cur: 1, scale: 1, vp: null, zoom: 1,
      objs: new Map(), sel: null, armed: null,
      seal: null, sign: null,
      dia: DIA_DEFAULT, ang: ANG_DEFAULT, signW: SIGNW_DEFAULT,
      seq: 1
    };
    container.innerHTML = TPL;

    /* 选 PDF */
    $('stPickPdf').onclick = () => $('stFilePdf').click();
    $('stFilePdf').onchange = async e => {
      const f = e.target.files[0];
      if (!f) return;
      e.target.value = '';
      log('', '读取 ' + f.name + ' …');
      try {
        const buf = await f.arrayBuffer();
        S.file = f;
        S.pdf = await pdfjsLib.getDocument({ data: buf }).promise;
        S.pages = S.pdf.numPages;
        S.objs.clear(); S.seq = 1; S.zoom = 1;
        $('stPdfName').textContent = f.name;
        $('stPdfInfo').textContent = S.pages + ' 页 · 点一下可换文件';
        $('stPickPdf').classList.add('on');
        await buildThumbs();
        await goPage(S.pages);
        syncUI();
        log('ok', '已打开 ' + S.pages + ' 页，默认显示最后一页');
        if (!S.seal && !S.sign) log('', '接着上传公章或签字，再点页面就能放上去');
      } catch (err) {
        log('warn', '这个 PDF 打不开：' + (err && err.message ? err.message : err));
      }
    };

    /* 素材 */
    const bindArt = (kind, pickId, fileId, nameId, thumbId) => {
      $(pickId).onclick = () => $(fileId).click();
      $(fileId).onchange = async e => {
        const f = e.target.files[0];
        if (!f) return;
        e.target.value = '';
        try {
          const a = await trimImage(f);
          S[kind] = a;
          $(nameId).textContent = f.name;
          const th = $(thumbId);
          th.innerHTML = '';
          const im = document.createElement('img');
          im.src = a.url;
          th.appendChild(im);
          $(pickId).classList.add('on');
          applySize();
          syncUI();
          log('ok', '已载入' + (kind === 'seal' ? '公章' : '签字') + '（' + a.w + '×' + a.h + '，已自动裁掉透明边）');
        } catch (err) {
          log('warn', '图片读不出来：' + (err && err.message ? err.message : err));
        }
      };
    };
    bindArt('seal', 'stPickSeal', 'stFileSeal', 'stSealName', 'stSealThumb');
    bindArt('sign', 'stPickSign', 'stFileSign', 'stSignName', 'stSignThumb');

    /* 滑块 */
    $('stDia').oninput = e => setDia(Number(e.target.value));
    $('stAng').oninput = e => setAng(Number(e.target.value));
    $('stSignW').oninput = e => setSignW(Number(e.target.value));
    $('stDiaReset').onclick = () => { setDia(DIA_DEFAULT); $('stDia').blur(); log('', '公章直径复位为 ' + DIA_DEFAULT + ' mm'); };
    $('stAngReset').onclick = () => { setAng(ANG_DEFAULT); $('stAng').blur(); log('', '公章角度复位为 0°'); };
    $('stSignWReset').onclick = () => { setSignW(SIGNW_DEFAULT); $('stSignW').blur(); log('', '签字宽度复位为 ' + SIGNW_DEFAULT + ' mm'); };

    /* 放置模式 */
    $('stArmSeal').onclick = () => { S.armed = S.armed === 'seal' ? null : 'seal'; syncUI(); if (S.armed) log('', '点页面上任意位置就能盖这个章，再点一次按钮取消'); };
    $('stArmSign').onclick = () => { S.armed = S.armed === 'sign' ? null : 'sign'; syncUI(); if (S.armed) log('', '点页面上任意位置就能放签字，再点一次按钮取消'); };
    $('stDel').onclick = () => deleteSel();
    $('stAuto').onclick = () => autoLocate();
    $('stGo').onclick = () => exportPdf();

    bindStage();
    syncReset();
    syncUI();

    /* 键盘 */
    keyAc = new AbortController();
    document.addEventListener('keydown', ev => {
      if (ev.key !== 'Delete' && ev.key !== 'Backspace') return;
      if (!S || !S.sel) return;
      // 只在真正能打字的控件里放过 Delete；range/checkbox/button 拿到焦点时
      // 仍然要能删图章 —— 拖过滑块之后焦点会留在滑块上，这是最容易踩的坑。
      if (isTextEntry(ev.target)) return;
      ev.preventDefault();
      deleteSel();
    }, { signal: keyAc.signal });

    document.addEventListener('keydown', ev => {
      if (!S || !S.sel) return;
      const step = ev.shiftKey ? 10 : 1;
      const map = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
      const d = map[ev.key];
      if (!d) return;
      ev.preventDefault();
      S.sel.cx += d[0]; S.sel.cy += d[1];
      renderObjs();
    }, { signal: keyAc.signal });

    window.addEventListener('resize', () => { if (S && S.pdf) goPage(S.cur); }, { signal: keyAc.signal });
  }

  return { mount: mount };
})();
