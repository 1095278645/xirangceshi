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

// 渲染函数已拆到 finance_render.js（避免单文件 >250 行）