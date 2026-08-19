// 记账页（依赖 core.js 的 state/api/toast/render/fmt）
'use strict';

// ---------- 记账 ----------
async function submitOrder(text) {
  state.submitting = true;
  state.result = text;
  state.manualText = '';
  render();
  try {
    const res = await api('/api/orders', 'POST', { text });
    state.parsed = res.parsed;
    state.voucher = res.voucher;
    state.friendlyCategory = res.friendly_category;
    state.summary = res.summary;
    if (res.amount_missing) toast('金额没听清，只记了流水');
  } catch (e) {
    toast(e.message);
  } finally {
    state.submitting = false;
    render();
    loadMonth();
  }
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
    <div class="review-box">${state.review}</div>
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
    <div class="voice-result">${state.result || ''}</div>
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

  ${p ? `
  <div class="card">
    <div class="card-title">已记下</div>
    <div class="type-badge ${p.trans_type === 'income' ? 'badge-income' : 'badge-expense'}">${p.trans_type === 'income' ? '收入' : '支出'}</div>
    <div class="parsed-grid">
      <div class="parsed-item"><span class="parsed-label">顾客</span><span class="parsed-value">${p.customer || '散客'}</span></div>
      <div class="parsed-item"><span class="parsed-label">事由</span><span class="parsed-value">${p.item || ''}</span></div>
      <div class="parsed-item"><span class="parsed-label">金额</span><span class="parsed-value">${p.amount != null ? p.amount + ' 元' : '未提'}</span></div>
      <div class="parsed-item"><span class="parsed-label">分类</span><span class="parsed-value">${state.friendlyCategory || p.category}</span></div>
      ${state.voucher ? `<div class="parsed-item"><span class="parsed-label">凭证</span><span class="parsed-value voucher-no">${state.voucher.voucher_no}（借:${state.voucher.debit} / 贷:${state.voucher.credit}）</span></div>` : ''}
    </div>
  </div>` : ''}

  <div class="card">
    <div class="card-title">不方便说话？直接打字</div>
    <div class="form-item">
      <textarea class="form-textarea" placeholder="比如：李师傅拿了两斤排骨，38块"
        oninput="state.manualText=this.value">${state.manualText}</textarea>
    </div>
    <button class="btn-primary ${state.submitting ? 'disabled' : ''}" onclick="submitManual()">${state.submitting ? '记账中…' : '记一笔'}</button>
  </div>`;
}