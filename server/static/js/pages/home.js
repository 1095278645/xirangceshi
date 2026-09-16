// 记账页（依赖 core.js 的 state/api/toast/render/fmt）
'use strict';

// ---------- 记账 ----------
async function submitOrder(text, extra) {
  state.submitting = true;
  state.result = text;
  state.manualText = '';
  render();
  try {
    const res = await api('/api/orders', 'POST', Object.assign({ text }, extra || {}));
    state.parsed = res.parsed;
    state.voucher = res.voucher;
    state.friendlyCategory = res.friendly_category;
    state.summary = res.summary;
    // 金额没听懂：**没有落库**，进入"补金额"状态（旧版会留一条 0 元幽灵记录）
    if (res.amount_missing) {
      state.amountDraft = res.draft || null;
      state.amountInput = '';
      state.recorded = null;
      return;
    }
    state.recorded = res.recorded || null;
    state.amountDraft = null;
    if (res.safety_warning) toast('⚠️ ' + res.safety_warning.message);
  } catch (e) {
    toast(e.message);
  } finally {
    state.submitting = false;
    render();
    if (!state.amountDraft) loadMonth();
  }
}

// ---------- 补金额（AI 没听懂金额时的追问） ----------
function onAmountInput(v) { state.amountInput = v; }

async function confirmAmount() {
  const d = state.amountDraft;
  if (!d) return;
  const amt = parseFloat(state.amountInput);
  if (!(amt > 0)) { toast('填一个大于 0 的金额'); return; }
  // 把 AI 已解析好的字段原样带回去：后端不重解析，科目/熟客与草稿一致
  await submitOrder(d.text || d.item, {
    amount: amt, customer: d.customer, item: d.item,
    category: d.category, trans_type: d.trans_type, note: d.note,
  });
  if (state.recorded) toast('已补记 ' + amt + ' 元');
}

function cancelAmount() {
  state.amountDraft = null;
  state.amountInput = '';
  state.parsed = null;
  render();
}

// ---------- 就地更正（AI 记错了当场改，不用等到月底对账） ----------
async function fixRecorded() {
  const r = state.recorded;
  if (!r || !r.transaction_id) return;
  const which = prompt('改什么？填 1=改金额  2=改成支出  3=改成收入', '1');
  if (which === null) return;
  const choice = String(which).trim();
  try {
    if (choice === '1') {
      const v = prompt('改成多少？', String(r.amount));
      if (v === null) return;
      const amt = parseFloat(v);
      if (isNaN(amt) || amt < 0) { toast('金额不对'); return; }
      const res = await api('/api/transactions/' + r.transaction_id, 'POST',
                            { amount: amt, reason: '店主当场更正金额' });
      state.recorded = Object.assign({}, r, { amount: (res.transaction || {}).amount });
    } else if (choice === '2' || choice === '3') {
      const want = choice === '2' ? 'expense' : 'income';
      if (want === r.trans_type) { toast('本来就是' + (want === 'income' ? '收入' : '支出')); return; }
      const res = await api('/api/transactions/' + r.transaction_id, 'POST',
                            { trans_type: want, reason: '店主当场更正收支方向' });
      state.recorded = Object.assign({}, r, { trans_type: (res.transaction || {}).trans_type });
    } else {
      return;
    }
    toast('已更正（留痕可查）');
  } catch (e) { toast(e.message); }
  render();
  loadMonth();
}

async function loadHome() {
  try {
    const [s, m, hb] = await Promise.all([
      api('/api/orders/today'), api('/api/orders/monthly'), api('/api/heartbeat')]);
    state.summary = s;
    state.month = m;
    state.review = hb.ok ? (hb.review || '') : '';
  } catch (_) {}
  render();
}

async function loadMonth() {
  try { state.month = await api('/api/orders/monthly'); } catch (_) {}
  render();
}

function submitManual() {
  const t = state.manualText.trim();
  if (!t) { toast('说点啥呢'); return; }
  submitOrder(t);
}

// ---------- 渲染 ----------
function renderHome() {
  const p = state.parsed;
  return `
  <div class="hero">
    <div class="hero-title">老板，今天辛苦啦！</div>
    <div class="hero-sub">巷子里的早餐铺 · AI掌柜已就位</div>
  </div>

  ${state.review ? `
  <div class="card">
    <div class="card-title">📋 掌柜今日复盘</div>
    <div class="review-box">${esc(state.review)}</div>
  </div>` : ''}

  <div class="voice-card">
    <div class="voice-hint ${state.recognizing ? 'recording' : ''}">
      ${state.voiceSupported
        ? (state.recognizing ? '正在听…说完了松手' : '按住说话，记一笔')
        : '语音需 HTTPS 或 localhost，请用下方手动输入'}
    </div>
    <button class="voice-btn ${state.recognizing ? 'recording' : ''} ${state.voiceSupported ? '' : 'disabled'}"
      ontouchstart="startRecord()" onmousedown="startRecord()"
      ontouchend="stopRecord()" onmouseup="stopRecord()" onmouseleave="stopRecord()">
      ${state.recognizing ? '🔴' : '🎤'}
    </button>
    <div class="voice-result">${esc(state.result)}</div>
  </div>

  <div class="card">
    <div class="card-title">今日账本</div>
    <div class="summary-row">
      <div class="summary-item"><div class="summary-num income">${fmt(state.summary.income)}</div><div class="summary-label">收入</div></div>
      <div class="summary-item"><div class="summary-num expense">${fmt(state.summary.expense)}</div><div class="summary-label">支出</div></div>
      <div class="summary-item"><div class="summary-num">${fmt(state.summary.balance)}</div><div class="summary-label">结余</div></div>
      <div class="summary-item"><div class="summary-num">${state.summary.cnt}</div><div class="summary-label">笔数</div></div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">本月账本 · ${state.month.period || ''}</div>
    <div class="summary-row">
      <div class="summary-item"><div class="summary-num income">${fmt(state.month.income)}</div><div class="summary-label">收入</div></div>
      <div class="summary-item"><div class="summary-num expense">${fmt(state.month.expense)}</div><div class="summary-label">支出</div></div>
      <div class="summary-item"><div class="summary-num">${fmt(state.month.balance)}</div><div class="summary-label">结余</div></div>
    </div>
  </div>

  ${state.amountDraft ? `
  <div class="card ask-card">
    <div class="card-title">这笔多少钱？</div>
    <div class="acct-note">这句话里没听出金额，<strong>没有记进账本</strong>——补上我就记。</div>
    <div class="parsed-grid">
      <div class="parsed-item"><span class="parsed-label">顾客</span><span class="parsed-value">${esc(state.amountDraft.customer || '散客')}</span></div>
      <div class="parsed-item"><span class="parsed-label">事由</span><span class="parsed-value">${esc(state.amountDraft.item || '')}</span></div>
      <div class="parsed-item"><span class="parsed-label">方向</span><span class="parsed-value">${state.amountDraft.trans_type === 'income' ? '收入' : '支出'}</span></div>
      <div class="parsed-item"><span class="parsed-label">分类</span><span class="parsed-value">${esc(state.amountDraft.category || '')}</span></div>
    </div>
    <div class="ask-row">
      <input class="ask-input" type="number" inputmode="decimal" placeholder="填金额，如 12.5"
             value="${esc(state.amountInput)}" oninput="onAmountInput(this.value)"
             onkeydown="if(event.key==='Enter')confirmAmount()" />
      <button class="ask-btn" onclick="confirmAmount()">记下</button>
    </div>
    <div class="ask-cancel" onclick="cancelAmount()">这次不记了</div>
  </div>` : ''}

  ${p ? `
  <div class="card">
    <div class="card-title">${state.amountDraft ? '待补金额' : '已记下'}</div>
    <div class="type-badge ${p.trans_type === 'income' ? 'badge-income' : 'badge-expense'}">${p.trans_type === 'income' ? '收入' : '支出'}</div>
    <div class="parsed-grid">
      <div class="parsed-item"><span class="parsed-label">顾客</span><span class="parsed-value">${esc(p.customer || '散客')}</span></div>
      <div class="parsed-item"><span class="parsed-label">事由</span><span class="parsed-value">${esc(p.item || '')}</span></div>
      <div class="parsed-item"><span class="parsed-label">金额</span><span class="parsed-value">${p.amount != null ? esc(p.amount) + ' 元' : '未提'}</span></div>
      <div class="parsed-item"><span class="parsed-label">分类</span><span class="parsed-value">${esc(state.friendlyCategory || p.category)}</span></div>
      ${state.voucher ? `<div class="parsed-item"><span class="parsed-label">凭证</span><span class="parsed-value voucher-no">${esc(state.voucher.voucher_no)}（借:${esc(state.voucher.debit)} / 贷:${esc(state.voucher.credit)}）</span></div>` : ''}
    </div>
  </div>` : ''}

  ${state.recorded ? `
  <div class="card recorded-card">
    <div class="card-title">我这么记的，对吗？</div>
    <div class="rec-line">
      <span class="rec-amount ${state.recorded.trans_type === 'income' ? 'in' : 'out'}">
        ${state.recorded.trans_type === 'income' ? '+' : '-'}${fmt(state.recorded.amount)}</span>
      <span class="rec-item">${esc(state.recorded.item || '')}</span>
    </div>
    <div class="rec-meta">
      ${esc(state.recorded.customer || '散客')} · ${esc(state.recorded.friendly_category || state.recorded.category || '')}
      ${state.recorded.category_normalized
        ? `<span class="rec-note">（原话是「${esc(state.recorded.raw_category)}」，已归到「${esc(state.recorded.category)}」）</span>`
        : ''}
    </div>
    <div class="rec-actions"><button class="btn-mini" onclick="fixRecorded()">记错了？点这里改</button></div>
  </div>` : ''}

  <div class="card">
    <div class="card-title">不方便说话？直接打字</div>
    <div class="form-item">
      <textarea class="form-textarea" placeholder="比如：李师傅拿了两斤排骨，38块"
        oninput="state.manualText=this.value">${esc(state.manualText)}</textarea>
    </div>
    <button class="btn-primary ${state.submitting ? 'disabled' : ''}" onclick="submitManual()">${state.submitting ? '记账中…' : '记一笔'}</button>
  </div>`;
}