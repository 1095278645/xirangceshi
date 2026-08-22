// 财务页：现金流滚动预测 / 预算 / 应收应付账龄（依赖 core.js 的 state/api/toast/render/esc/pad2/fmt）
'use strict';

// 默认当月 YYYY-MM
function finMonth() {
  const n = new Date();
  return `${n.getFullYear()}-${pad2(n.getMonth() + 1)}`;
}

async function loadFinance() {
  if (!state.finance.month) state.finance.month = finMonth();
  if (state.finance.tab === 1) await loadBudgets();
  else if (state.finance.tab === 2) await loadDebts();
  else { state.finance.cash.loading = true; render(); state.finance.cash.loading = false; }
  render();
}

function switchFinTab(i) {
  state.finance.tab = i;
  render();
  if (i === 1) loadBudgets();
  else if (i === 2) loadDebts();
}

// ---------------- 现金流滚动预测 ----------------
async function runCashflow() {
  const cash = parseFloat(state.finance.cash.cash_on_hand);
  if (!(cash >= 0)) { toast('先填现有现金'); return; }
  state.finance.cash.loading = true;
  state.finance.cash.result = null;
  render();
  try {
    state.finance.cash.result = await api('/api/cashflow', 'POST', {
      cash_on_hand: cash, months: parseInt(state.finance.cash.months) || 6 });
  } catch (e) { toast(e.message); }
  state.finance.cash.loading = false;
  render();
}

// ---------------- 预算 ----------------
async function loadBudgets() {
  const m = state.finance.month || finMonth();
  state.finance.budLoading = true;
  render();
  try {
    const [budgets, vs] = await Promise.all([
      api(`/api/budgets?month=${m}`),
      api(`/api/budgets/actual?month=${m}`),
    ]);
    state.finance.budgets = budgets;
    state.finance.budVs = vs;
  } catch (e) { toast(e.message); }
  state.finance.budLoading = false;
  render();
}

async function saveBudget() {
  const f = state.finance.budForm;
  const amount = parseFloat(f.amount);
  if (!(amount >= 0)) { toast('先填预算金额'); return; }
  try {
    await api('/api/budgets', 'POST', {
      month: f.month || state.finance.month, scope: f.scope, amount,
      category: f.category, note: f.note });
    f.amount = ''; f.category = ''; f.note = '';
    toast('预算已保存');
    await loadBudgets();
  } catch (e) { toast(e.message); }
}

async function deleteBudget(id) {
  if (!confirm('删除这条预算？')) return;
  try { await api(`/api/budgets/${id}`, 'DELETE'); await loadBudgets(); }
  catch (e) { toast(e.message); }
}

// ---------------- 应收应付 ----------------
async function loadDebts() {
  state.finance.debtLoading = true;
  render();
  try {
    const [debts, aging] = await Promise.all([
      api('/api/debts'), api('/api/debts/aging')]);
    state.finance.debts = debts;
    state.finance.aging = aging;
  } catch (e) { toast(e.message); }
  state.finance.debtLoading = false;
  render();
}

async function addDebt() {
  const f = state.finance.debtForm;
  const amount = parseFloat(f.amount);
  if (!(amount > 0)) { toast('先填金额'); return; }
  try {
    await api('/api/debts', 'POST', {
      party: f.party, kind: f.kind, amount,
      due_date: f.due_date || '', note: f.note });
    f.party = ''; f.amount = ''; f.due_date = ''; f.note = '';
    toast('已记下这笔账');
    await loadDebts();
  } catch (e) { toast(e.message); }
}

async function settleDebt(id) {
  if (!confirm('结清这笔账（全部）？')) return;
  try { await api(`/api/debts/${id}/settle`, 'POST', {}); await loadDebts(); }
  catch (e) { toast(e.message); }
}

async function deleteDebt(id) {
  if (!confirm('删除这笔账？')) return;
  try { await api(`/api/debts/${id}`, 'DELETE'); await loadDebts(); }
  catch (e) { toast(e.message); }
}

// ---------------- 渲染 ----------------
function renderFinance() {
  const fn = state.finance;
  const m = fn.month || finMonth();
  const tab = ['现金流', '预算', '应收应付'];
  const seg = tab.map((t, i) =>
    `<div class="seg-item ${fn.tab === i ? 'active' : ''}" onclick="switchFinTab(${i})">${t}</div>`).join('');

  let body = '';
  if (fn.tab === 0) {
    body = `
    <div class="card">
      <div class="card-title">💰 现金流看看能撑多久</div>
      <div class="form-item"><label class="form-label">现有现金（元）</label>
        <input class="form-input" type="number" inputmode="decimal" placeholder="如 50000"
          value="${esc(fn.cash.cash_on_hand)}" oninput="state.finance.cash.cash_on_hand=this.value" /></div>
      <div class="form-item"><label class="form-label">预测几个月</label>
        <input class="form-input" type="number" inputmode="numeric" min="1" max="24"
          value="${fn.cash.months}" oninput="state.finance.cash.months=this.value" /></div>
      <button class="btn-primary" onclick="runCashflow()">${fn.cash.loading ? '计算中…' : '预测未来收支'}</button>
      ${fn.cash.result ? `
      <div class="result-box">
        <div class="store-verdict">${esc(fn.cash.result.summary)}</div>
      </div>
      <div class="store-grid">
        ${fn.cash.result.months.map(r => `
        <div class="store-cell">
          <div class="store-cell-num ${r.end_balance < 0 ? 'expense' : 'income'}">${fmt(r.end_balance)}</div>
          <div class="store-cell-label">${r.month} 底现金 ${r.safe ? '' : '⚠️'}</div>
        </div>`).join('')}
      </div>
      ${fn.cash.result.flags.length ? `
      <div class="result-box">
        ${fn.cash.result.flags.map(f => `<div class="note">${esc(f)}</div>`).join('')}
      </div>` : ''}` : ''}
    </div>
    <div class="howto-text" style="padding:0 16px 4px">按账本近 3 个月均线 + 应收应付到期日算。收入、支出、赊账到期都会影响现金，多看几个月心里有底。</div>`;
  } else if (fn.tab === 1) {
    body = `
    <div class="card">
      <div class="card-title">📋 月度预算
        <input type="month" value="${m}" class="month-input" onchange="state.finance.month=this.value;loadBudgets()" />
      </div>
      <div class="form-item"><label class="form-label">类型</label>
        <div class="pay-type-toggle">
          <div class="pay-type-btn ${fn.budForm.scope === 'expense' ? 'on' : ''}" onclick="state.finance.budForm.scope='expense';render()">支出计划</div>
          <div class="pay-type-btn ${fn.budForm.scope === 'income' ? 'on' : ''}" onclick="state.finance.budForm.scope='income';render()">进账目标</div>
        </div>
      </div>
      <div class="form-item"><label class="form-label">金额（元）</label>
        <input class="form-input" type="number" inputmode="decimal" placeholder="如 8000"
          value="${esc(fn.budForm.amount)}" oninput="state.finance.budForm.amount=this.value" /></div>
      <div class="form-item"><label class="form-label">分类（可留空=全部）</label>
        <input class="form-input" type="text" placeholder="如 进货"
          value="${esc(fn.budForm.category)}" oninput="state.finance.budForm.category=this.value" /></div>
      <button class="btn-primary" onclick="saveBudget()">保存预算</button>
    </div>
    ${fn.budVs ? `
    <div class="card">
      <div class="card-title">本月计划 vs 实际</div>
      ${fn.budVs.summary ? `<div class="review-box">${esc(fn.budVs.summary)}</div>` : ''}
      ${fn.budVs.flags.map(f => `<div class="note warn-box">${esc(f)}</div>`).join('')}
      ${fn.budVs.plan_total.expense ? `<div class="dim-row"><span class="dim-name">计划支出</span><span class="dim-level">${fmt(fn.budVs.plan_total.expense)} 元</span></div>` : ''}
      ${fn.budVs.plan_total.income ? `<div class="dim-row"><span class="dim-name">计划进账</span><span class="dim-level">${fmt(fn.budVs.plan_total.income)} 元</span></div>` : ''}
      <div class="dim-row"><span class="dim-name">实际支出</span><span class="dim-level">${fmt(fn.budVs.actual.expense)} 元</span></div>
      <div class="dim-row"><span class="dim-name">实际进账</span><span class="dim-level">${fmt(fn.budVs.actual.income)} 元</span></div>
    </div>` : ''}
    <div class="card">
      <div class="card-title">预算清单</div>
      ${fn.budgets.length === 0 ? '<div class="empty">还没设过预算</div>' : fn.budgets.map(b => `
      <div class="txn-row">
        <div class="txn-main">
          <div class="txn-item">${b.scope === 'expense' ? '支出' : '进账'} · ${esc(b.category || '全部')}</div>
          <div class="txn-sub">${esc(b.note || '')}${b.note ? ' · ' : ''}${b.month}</div>
        </div>
        <div class="txn-amount">${fmt(b.amount)}</div>
        <div class="pay-row-actions"><button class="btn-mini btn-danger" onclick="deleteBudget(${b.id})">删</button></div>
      </div>`).join('')}
    </div>`;
  } else {
    body = `
    ${fn.aging && fn.aging.flags.length ? `
    <div class="card">
      <div class="card-title">⏰ 该催/该还的</div>
      ${fn.aging.flags.map(f => `<div class="note warn">${esc(f)}</div>`).join('')}
    </div>` : ''}
    <div class="card">
      <div class="card-title">记一笔赊账</div>
      <div class="form-item"><label class="form-label">类型</label>
        <div class="pay-type-toggle">
          <div class="pay-type-btn ${fn.debtForm.kind === 'receivable' ? 'on' : ''}" onclick="state.finance.debtForm.kind='receivable';render()">别人欠我（应收）</div>
          <div class="pay-type-btn ${fn.debtForm.kind === 'payable' ? 'on' : ''}" onclick="state.finance.debtForm.kind='payable';render()">我欠别人（应付）</div>
        </div>
      </div>
      <div class="form-item"><label class="form-label">对方（谁欠你/你欠谁）</label>
        <input class="form-input" type="text" placeholder="如 王姐" value="${esc(fn.debtForm.party)}" oninput="state.finance.debtForm.party=this.value" /></div>
      <div class="form-item"><label class="form-label">金额（元）</label>
        <input class="form-input" type="number" inputmode="decimal" placeholder="如 3000" value="${esc(fn.debtForm.amount)}" oninput="state.finance.debtForm.amount=this.value" /></div>
      <div class="form-item"><label class="form-label">到期日</label>
        <input class="form-input" type="date" value="${esc(fn.debtForm.due_date)}" onchange="state.finance.debtForm.due_date=this.value" /></div>
      <button class="btn-primary" onclick="addDebt()">记下这笔账</button>
    </div>
    <div class="card">
      <div class="card-title">📒 应收（谁欠我钱）</div>
      ${!fn.debts.some(d => d.kind === 'receivable') ? '<div class="empty">没有应收</div>' :
        fn.debts.filter(d => d.kind === 'receivable').map(d => `
      <div class="txn-row">
        <div class="txn-main">
          <div class="txn-item">${esc(d.party || '未记名')} ${d.status === 'settled' ? '<span class="type-badge badge-expense">已结</span>' : ''}</div>
          <div class="txn-sub">${d.due_date ? '到期 ' + esc(d.due_date) : ''}${d.aging_bucket ? ' · ' + esc(d.aging_bucket) : ''}</div>
        </div>
        <div class="txn-amount income">${fmt(d.balance)}</div>
        ${d.status === 'open' ? `<div class="pay-row-actions"><button class="btn-mini" onclick="settleDebt(${d.id})">结清</button></div>` : ''}
        <div class="pay-row-actions"><button class="btn-mini btn-danger" onclick="deleteDebt(${d.id})">删</button></div>
      </div>`).join('')}
    </div>
    <div class="card">
      <div class="card-title">📕 应付（我欠谁钱）</div>
      ${!fn.debts.some(d => d.kind === 'payable') ? '<div class="empty">没有欠账</div>' :
        fn.debts.filter(d => d.kind === 'payable').map(d => `
      <div class="txn-row">
        <div class="txn-main">
          <div class="txn-item">${esc(d.party || '未记名')} ${d.status === 'settled' ? '<span class="type-badge badge-expense">已结</span>' : ''}</div>
          <div class="txn-sub">${d.due_date ? '到期 ' + esc(d.due_date) : ''}${d.aging_bucket ? ' · ' + esc(d.aging_bucket) : ''}</div>
        </div>
        <div class="txn-amount expense">${fmt(d.balance)}</div>
        ${d.status === 'open' ? `<div class="pay-row-actions"><button class="btn-mini" onclick="settleDebt(${d.id})">还</button></div>` : ''}
        <div class="pay-row-actions"><button class="btn-mini btn-danger" onclick="deleteDebt(${d.id})">删</button></div>
      </div>`).join('')}
    </div>`;
  }

  return `
  <div class="hero"><div class="hero-title">财务</div><div class="hero-sub">现金流 · 预算 · 赊账账龄，心里有底</div></div>
  <div class="seg">${seg}</div>
  ${body}`;
}