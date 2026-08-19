// 熟客（依赖 core.js 的 state/api/toast/render，go 用于返回）
'use strict';

// ---------- 熟客 ----------
async function loadCustomers() {
  try { state.customers = await api('/api/customers'); } catch (_) {}
  render();
}

async function viewCustomer(id) {
  try {
    state.custDetail = await api('/api/customers/' + id);
    state.custInsight = null;
    state.custInsightAiUsed = false;
    state.route = 'custDetail';
    render();
    loadCustInsight(id);
  } catch (e) { toast(e.message); }
}

async function loadCustInsight(id) {
  state.custInsightLoading = true;
  render();
  try {
    const r = await api('/api/customers/' + id + '/insight', 'POST', {});
    state.custInsight = r.insight;
    state.custInsightAiUsed = r.ai_used;
  } catch (_) { state.custInsight = null; }
  state.custInsightLoading = false;
  render();
}

async function addMemory() {
  if (!state.custMemInput.trim()) { toast('写点啥呢'); return; }
  try {
    await api('/api/memories', 'POST', { customer_id: state.custDetail.id, content: state.custMemInput.trim() });
    state.custMemInput = '';
    toast('记下了');
    state.custDetail = await api('/api/customers/' + state.custDetail.id);
    render();
  } catch (e) { toast(e.message); }
}

// ---------- 渲染 ----------
function renderCustomers() {
  const cs = state.customers;
  return `
  <div class="hero"><div class="hero-title">熟客记忆</div><div class="hero-sub">老主顾的脸和事，帮你记着</div></div>
  <div class="card">
    <div class="card-title">熟客列表（${cs.length}）</div>
    ${cs.length === 0 ? '<div class="empty">记账时提到称呼会自动建档</div>' :
      cs.map(c => `
      <div class="cust-item" onclick="viewCustomer(${c.id})">
        <div>
          <div class="cust-name">${c.name}</div>
          <div class="cust-meta">常点：${c.favorite || '未知'} · 上次：${(c.last_visit || '').slice(5, 16)}</div>
        </div>
        <span class="cust-arrow">›</span>
      </div>`).join('')}
  </div>`;
}

function renderCustDetail() {
  const c = state.custDetail;
  if (!c) return '';
  return `
  <div class="hero"><div class="hero-title">${c.name}</div><div class="hero-sub">常点：${c.favorite || '未知'}</div></div>
  <div class="card">
    <div class="card-title">基本信息</div>
    <div class="parsed-grid">
      <div class="parsed-item"><span class="parsed-label">电话</span><span class="parsed-value">${c.phone || '未留'}</span></div>
      <div class="parsed-item"><span class="parsed-label">标签</span><span class="parsed-value">${c.tags || '无'}</span></div>
      <div class="parsed-item"><span class="parsed-label">上次到店</span><span class="parsed-value">${c.last_visit || '未知'}</span></div>
    </div>
  </div>
  ${state.custInsightLoading ? '<div class="card"><div class="card-title">📊 画像分析</div><div class="empty">分析中…</div></div>' : ''}
  ${state.custInsight ? `<div class="card"><div class="card-title">📊 画像分析 ${state.custInsightAiUsed ? '✨' : '📝'}</div><div class="review-box">${state.custInsight}</div></div>` : ''}
  <div class="card">
    <div class="card-title">记一笔关于他的事</div>
    <div class="form-item">
      <textarea class="form-textarea" placeholder="比如：孙子考了一百分、爱聊钓鱼"
        oninput="state.custMemInput=this.value">${state.custMemInput}</textarea>
    </div>
    <button class="btn-primary" onclick="addMemory()">记下</button>
  </div>
  <button class="btn-ghost" onclick="go('customers')">返回列表</button>`;
}