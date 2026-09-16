// 收款页（现场生成收款码 → 顾客扫码付款 → 一键入账）
// 依赖 core.js 的 state/api/toast/render/esc/fmt
//
// 为什么二维码要带 origin 参数：二维码里必须放**顾客手机能访问到的绝对地址**。
// 店主可能用 localhost 打开后台，而顾客要用局域网 IP —— 直接把请求的 base_url
// 编进二维码会生成 localhost（顾客扫了打不开）。网页端这里统一用 location.origin，
// 并把当前后端地址作为兜底。
'use strict';

async function loadCollect() {
  state.collect.loading = true;
  render();
  try {
    const r = await api('/api/collect/list');
    state.collect.list = r.collections || [];
    state.collect.pending = r.pending_count || 0;
  } catch (e) { toast(e.message); state.collect.list = []; }
  state.collect.loading = false;
  render();
}

// 顾客手机能访问到的地址：优先当前页面 origin（店主就是这么访问后台的）
function _publicOrigin() {
  const o = (typeof location !== 'undefined' && location.origin) || '';
  if (o && !/^https?:\/\/(localhost|127\.0\.0\.1)/.test(o)) return o;
  return o || '';
}

async function createCollection() {
  const c = state.collect;
  const amount = parseFloat(c.form.amount);
  if (!(amount > 0)) { toast('请填写收款金额'); return; }
  try {
    const r = await api('/api/collect/create', 'POST', {
      amount, item: c.form.item.trim() || '现场收款',
    });
    const col = r.collection;
    c.current = {
      token: col.token, amount: col.amount, item: col.item,
      pay_url: location.origin + '/pay/' + col.token,
      origin: _publicOrigin(),
    };
    c.form.amount = ''; c.form.item = '';
    toast('收款码已生成');
    await loadCollect();
    await loadQrSvg();
  } catch (e) { toast(e.message); }
}

// 取二维码 SVG（接口是店主侧的，需要带令牌，所以不能直接 <img src>）
async function loadQrSvg() {
  const c = state.collect;
  if (!c.current) return;
  c.qrLoading = true;
  render();
  try {
    const res = await fetch(
      `/api/collect/${encodeURIComponent(c.current.token)}/qr.svg` +
      `?origin=${encodeURIComponent(c.current.origin)}`,
      { headers: authHeaders() });
    if (!res.ok) throw new Error('二维码生成失败 ' + res.status);
    c.qrSvg = await res.text();
  } catch (e) { c.qrSvg = ''; toast(e.message); }
  c.qrLoading = false;
  render();
}

function closeQr() {
  state.collect.current = null;
  state.collect.qrSvg = '';
  render();
}

async function confirmCollection(cid) {
  try {
    const r = await api(`/api/collect/${cid}/confirm`, 'POST', {});
    const amt = r.amount || (r.collection && r.collection.amount) || '';
    toast(`已入账 ${fmt(amt)} 元` + (r.announce ? '，到账播报已发出' : ''));
    await loadCollect();
  } catch (e) { toast(e.message); }
}

async function cancelCollection(cid) {
  const reason = prompt('取消这笔收款的原因（可留空）');
  if (reason === null) return;
  try {
    await api(`/api/collect/${cid}/cancel`, 'POST', { reason: reason || '' });
    toast('已取消');
    await loadCollect();
  } catch (e) { toast(e.message); }
}

const COLLECT_STATUS = {
  pending: '待付款', paid: '已付款待确认', confirmed: '已入账', cancelled: '已取消',
};

// ---------------- 渲染 ----------------
function renderCollect() {
  const c = state.collect;

  const rows = c.list.length === 0
    ? '<div class="empty">还没有收款请求，填个金额生成收款码试试</div>'
    : c.list.map(x => `
      <div class="txn-row">
        <div class="txn-main">
          <div class="txn-item">${fmt(x.amount)} 元 · ${esc(x.item || '收款')}
            <span class="pay-status">${COLLECT_STATUS[x.status] || x.status}</span></div>
          <div class="txn-sub">${esc(x.created_at || '')}${x.payer_name ? ' · 顾客：' + esc(x.payer_name) : ''}</div>
        </div>
        <div class="pay-row-actions">
          ${(x.status === 'pending' || x.status === 'paid')
            ? `<button class="btn-mini" onclick="confirmCollection(${x.id})">入账</button>
               <button class="btn-mini" onclick="copyLink('${esc(location.origin + '/pay/' + x.token)}')">复制链接</button>
               <button class="btn-mini btn-danger" onclick="cancelCollection(${x.id})">取消</button>`
            : ''}
        </div>
      </div>`).join('');

  const qrCard = c.current ? `
    <div class="card">
      <div class="card-title">💳 收款码 · ${fmt(c.current.amount)} 元</div>
      <div class="acct-note">${esc(c.current.item || '')}</div>
      <div class="qr-holder">
        ${c.qrLoading ? '<div class="empty">生成中…</div>'
          : (c.qrSvg || '<div class="empty">二维码生成失败，可复制下方链接给顾客</div>')}
      </div>
      <div class="acct-note">请顾客用微信扫这个码付款；付完点页面上的「我已付款」，
        你这边点「入账」即可自动记账。</div>
      <div class="pay-actions">
        <button class="btn-mini" onclick="copyLink('${esc(c.current.pay_url)}')">复制收款链接</button>
        <button class="btn-mini" onclick="window.open('${esc(c.current.pay_url)}','_blank')">打开收款页</button>
        <button class="btn-mini btn-danger" onclick="closeQr()">关闭</button>
      </div>
    </div>` : '';

  return `
  <div class="hero"><div class="hero-title">收款</div>
    <div class="hero-sub">生成收款码 → 顾客扫码付款 → 一键入账（自动生成凭证）</div></div>

  ${qrCard}

  <div class="card">
    <div class="card-title">➕ 新建收款</div>
    <div class="form-item"><label class="form-label">金额（元）</label>
      <input class="form-input" type="number" inputmode="decimal" placeholder="如 25.5"
             value="${esc(c.form.amount)}" oninput="state.collect.form.amount=this.value" /></div>
    <div class="form-item"><label class="form-label">事由（可留空）</label>
      <input class="form-input" type="text" placeholder="如 早点"
             value="${esc(c.form.item)}" oninput="state.collect.form.item=this.value" /></div>
    <button class="btn-primary" onclick="createCollection()">生成收款码</button>
  </div>

  <div class="card">
    <div class="card-title">📋 收款记录${c.pending ? `（待处理 ${c.pending}）` : ''}</div>
    ${c.loading ? '<div class="empty">加载中…</div>' : rows}
  </div>

  <div class="card">
    <div class="card-title">ℹ️ 说明</div>
    <div class="acct-note">资金仍走店主自己的收款码，本功能只解决"收了钱还要手工记账"这一段。
      顾客填的称呼会自动建熟客档案；确认入账后会生成借贷凭证，并可按配置推送「到账播报」。</div>
  </div>`;
}
