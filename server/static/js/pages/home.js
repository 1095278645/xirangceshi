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
      state.amountMissing = res.missing || [];   // 多笔时缺的是哪几笔
      state.amountInput = '';
      state.recorded = null;
      state.recordedList = [];
      return;
    }
    state.recorded = res.recorded || null;
    // 一句话多笔：逐条列出，每条都能单独改
    state.recordedList = res.recorded_list || (res.recorded ? [res.recorded] : []);
    state.multi = !!res.multi;
    state.amountMissing = res.missing || [];
    state.amountDraft = null;
    if (res.multi) toast(`听出 ${state.recordedList.length} 笔，都记上了`);
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
async function fixRecorded(index) {
  const list = state.recordedList || [];
  const i = (typeof index === 'number' && list[index]) ? index : -1;
  const r = i >= 0 ? list[i] : state.recorded;
  if (!r || !r.transaction_id) return;
  const which = prompt('改什么？填 1=改金额  2=改成支出  3=改成收入', '1');
  if (which === null) return;
  const choice = String(which).trim();
  try {
    let merged = r;
    if (choice === '1') {
      const v = prompt('改成多少？', String(r.amount));
      if (v === null) return;
      const amt = parseFloat(v);
      if (isNaN(amt) || amt < 0) { toast('金额不对'); return; }
      const res = await api('/api/transactions/' + r.transaction_id, 'POST',
                            { amount: amt, reason: '店主当场更正金额' });
      merged = Object.assign({}, r, { amount: (res.transaction || {}).amount });
    } else if (choice === '2' || choice === '3') {
      const want = choice === '2' ? 'expense' : 'income';
      if (want === r.trans_type) { toast('本来就是' + (want === 'income' ? '收入' : '支出')); return; }
      const res = await api('/api/transactions/' + r.transaction_id, 'POST',
                            { trans_type: want, reason: '店主当场更正收支方向' });
      merged = Object.assign({}, r, { trans_type: (res.transaction || {}).trans_type });
    } else {
      return;
    }
    if (i >= 0) {
      const next = list.slice();
      next[i] = merged;
      state.recordedList = next;
      state.recorded = next[0];
    } else {
      state.recorded = merged;
      state.recordedList = [merged];
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
    state.review = (hb.review || '');
    state.snapshot = (hb.snapshot || '');
  } catch (_) {}
  render();
}

// 让掌柜立刻复盘一次（五位伙计各管一摊 → 掌柜裁决，约 5~10 秒）
async function reviewNow() {
  if (state.reviewBusy) return;
  state.reviewBusy = true;
  state.snapOpen = false;
  render();
  try {
    const h = await api('/api/heartbeat', 'POST', {});
    state.review = h.review || '';
    state.snapshot = h.snapshot || '';
    toast('掌柜已复盘');
  } catch (e) { toast(e.message); }
  state.reviewBusy = false;
  render();
}

function toggleSnap() { state.snapOpen = !state.snapOpen; render(); }

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

  ${(state.review || state.reviewBusy) ? `
  <div class="card review-card">
    <div class="review-head">
      <div class="card-title">🏮 掌柜今日复盘</div>
      <button class="btn-mini ${state.reviewBusy ? 'disabled' : ''}" onclick="reviewNow()">
        ${state.reviewBusy ? '掌柜在看账…' : '让掌柜再看一遍'}</button>
    </div>
    ${state.review
      ? `<div class="review-box">${esc(state.review)}</div>`
      : '<div class="acct-note">五位伙计正在各自看账（账目·熟客·库存·票税·监察），约 10 秒…</div>'}
    ${state.snapshot ? `
      <div class="snap-toggle" onclick="toggleSnap()">
        ${state.snapOpen ? '收起' : '掌柜看到的原始事实'} ▾</div>` : ''}
    ${state.snapOpen && state.snapshot ? `
      <div class="snap-box">${esc(state.snapshot).replace(/\n/g, '<br/>')}
        <div class="acct-note">结论都是从这些事实里挑出来的；没记的经营动作也会出现在这里。</div>
      </div>` : ''}
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
    <div class="card-title">${(state.amountMissing || []).length > 1
      ? `有 ${state.amountMissing.length} 笔没说金额`
      : '这笔多少钱？'}</div>
    <div class="acct-note">
      ${(state.amountMissing || []).length
        ? '这句话里听出好几笔，其中以下几笔没听出金额，<strong>一笔都没记</strong>——'
        : '这句话里没听出金额，<strong>没有记进账本</strong>——'}
      补上我就记。
    </div>
    ${(state.amountMissing || []).length ? `
      <div class="snap-box">${state.amountMissing.map(m =>
        `· ${esc(m.item || '')}（${esc(m.customer || '散客')}，`
        + `${m.trans_type === 'income' ? '收入' : '支出'}）`).join('<br/>')}</div>` : ''}
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

  ${state.recordedList && state.recordedList.length ? `
  <div class="card recorded-card">
    <div class="card-title">${state.multi
      ? `这句话我听出 ${state.recordedList.length} 笔，对吗？`
      : '我这么记的，对吗？'}</div>
    ${state.recordedList.map((r, i) => `
      <div class="rec-item-row">
        <div class="rec-line">
          <span class="rec-amount ${r.trans_type === 'income' ? 'in' : 'out'}">
            ${r.trans_type === 'income' ? '+' : '-'}${fmt(r.amount)}</span>
          <span class="rec-item">${esc(r.item || '')}</span>
        </div>
        <div class="rec-meta">
          ${esc(r.customer || '散客')} · ${esc(r.friendly_category || r.category || '')}
          ${r.category_normalized
            ? `<span class="rec-note">（原话是「${esc(r.raw_category)}」，已归到「${esc(r.category)}」）</span>`
            : ''}
        </div>
        <div class="rec-actions">
          <button class="btn-mini" onclick="fixRecorded(${i})">这条记错了？改</button></div>
      </div>`).join('')}
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