// 熟客（依赖 core.js 的 state/api/toast/render，go 用于返回）
'use strict';

// ---------- 熟客 ----------
let _reminders = [];   // 今日待办（OPC 执行闭环：生成 → 送达 → 完成，都能在这里看到）

async function loadCustomers() {
  try { state.customers = await api('/api/customers'); } catch (_) {}
  try {
    const r = await api('/api/reminders?done=0');
    _reminders = Array.isArray(r) ? r : (r.reminders || []);
  } catch (_) { _reminders = []; }
  render();
}

// 执行闭环：把提醒真的送出去（后端自动选通道：已订阅通道 > 本地记录）
async function sendReminder(rid) {
  try {
    const r = await api('/api/reminders/' + rid + '/send', 'POST', {});
    toast('已送达（' + (r.channel || '') + '）');
    await loadCustomers();
  } catch (e) { toast(e.message); await loadCustomers(); }
}

// 执行闭环：办完了就回填（完成态不再补发）
async function doneReminder(rid) {
  try {
    await api('/api/reminders/' + rid + '/done', 'POST', {});
    toast('已标记完成');
    await loadCustomers();
  } catch (e) { toast(e.message); }
}

// 增长动作（攻）：拉新 / 复购 / 选品提价
let _growth = { text: '', loading: false, aiUsed: false };
async function genGrowth() {
  _growth.loading = true; render();
  try {
    const r = await api('/api/insights', 'POST', { scene: 'growth', payload: {} });
    _growth.text = r.actions || '';
    _growth.aiUsed = !!r.ai_used;
  } catch (e) { toast(e.message); }
  _growth.loading = false; render();
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

// 竞态守卫：快速切换不同熟客时，丢弃先发但后到的过期响应
let _custInsightReq = 0;
async function loadCustInsight(id) {
  const myId = ++_custInsightReq;
  state.custInsightLoading = true;
  render();
  try {
    const r = await api('/api/insights', 'POST', { scene: 'customer', payload: { customer_id: id } });
    if (myId !== _custInsightReq) return;  // 过期响应，丢弃
    state.custInsight = r.insight;
    state.custInsightAiUsed = r.ai_used;
  } catch (_) {
    if (myId !== _custInsightReq) return;
    state.custInsight = null;
  }
  if (myId !== _custInsightReq) return;
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
    <div class="card-title">增长动作 · 拉新 / 复购 / 选品</div>
    ${_growth.loading ? '<div class="howto-text">生成中…（多 agent 会并行出稿，约 5~10 秒）</div>'
      : (_growth.text
        ? `<div class="review-box">${esc(_growth.text).replace(/\n/g, '<br/>')}</div>`
        : '<div class="howto-text">让 AI 结合熟客消费与库存，给出今天就能做的三条动作（无 Key 也能给规则化建议）。</div>')}
    <button class="btn-primary" onclick="genGrowth()">${_growth.text ? '重新生成' : '生成增长动作'}</button>
    ${_growth.text ? `<span class="howto-text">${_growth.aiUsed ? '✨ 真实 AI' : '📝 规则兜底'}</span>` : ''}
  </div>
  <div class="card">
    <div class="card-title">今日提醒（${_reminders.length}）<span class="howto-text" style="font-weight:400"> · AI 生成后会自动送达，未送达的次日自动重试</span></div>
    ${_reminders.length === 0 ? '<div class="empty">暂无待办：生成提醒或记账后，AI 会在这里排好今天该做的事</div>' :
      _reminders.map(r => `
      <div class="cust-item">
        <div>
          <div class="cust-name">${esc(r.customer_name || '熟客')}</div>
          <div class="cust-meta">${esc(r.content || '')}</div>
          <div class="cust-meta">${r.send_ok
            ? '✅ 已送达（' + esc(r.send_channel || '') + '）'
            : (r.send_error ? '⚠️ 上次投递失败：' + esc(r.send_error) : '尚未送达')}</div>
        </div>
        <div>
          <button class="btn-mini" onclick="sendReminder(${r.id})">发送</button>
          <button class="btn-mini" onclick="doneReminder(${r.id})">完成</button>
        </div>
      </div>`).join('')}
  </div>
  <div class="card">
    <div class="card-title">熟客列表（${cs.length}）</div>
    ${cs.length === 0 ? '<div class="empty">记账时提到称呼会自动建档</div>' :
      cs.map(c => `
      <div class="cust-item" onclick="viewCustomer(${c.id})">
        <div>
          <div class="cust-name">${esc(c.name)}</div>
          <div class="cust-meta">常点：${esc(c.favorite || '未知')} · 上次：${esc((c.last_visit || '').slice(5, 16))}</div>
        </div>
        <span class="cust-arrow">›</span>
      </div>`).join('')}
  </div>`;
}

function renderCustDetail() {
  const c = state.custDetail;
  if (!c) return '';
  return `
  <div class="hero"><div class="hero-title">${esc(c.name)}</div><div class="hero-sub">常点：${esc(c.favorite || '未知')}</div></div>
  <div class="card">
    <div class="card-title">基本信息</div>
    <div class="parsed-grid">
      <div class="parsed-item"><span class="parsed-label">电话</span><span class="parsed-value">${esc(c.phone || '未留')}</span></div>
      <div class="parsed-item"><span class="parsed-label">标签</span><span class="parsed-value">${esc(c.tags || '无')}</span></div>
      <div class="parsed-item"><span class="parsed-label">上次到店</span><span class="parsed-value">${esc(c.last_visit || '未知')}</span></div>
    </div>
  </div>
  ${state.custInsightLoading ? '<div class="card"><div class="card-title">📊 画像分析</div><div class="empty">分析中…</div></div>' : ''}
  ${state.custInsight ? `<div class="card"><div class="card-title">📊 画像分析 ${state.custInsightAiUsed ? '✨' : '📝'}</div><div class="review-box">${esc(state.custInsight)}</div></div>` : ''}
  <div class="card">
    <div class="card-title">记一笔关于他的事</div>
    <div class="form-item">
      <textarea class="form-textarea" placeholder="比如：孙子考了一百分、爱聊钓鱼"
        oninput="state.custMemInput=this.value">${esc(state.custMemInput)}</textarea>
    </div>
    <button class="btn-primary" onclick="addMemory()">记下</button>
  </div>
  <button class="btn-ghost" onclick="go('customers')">返回列表</button>`;
}
