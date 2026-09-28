// 会计报表页（利润表 / 资产负债表 / 科目余额表 / 期末结转）
// 依赖 core.js 的 state/api/toast/render/esc/fmt
//
// 这三张表是复式记账的"验收标准"：利润表看赚了多少，资产负债表看家底，
// 余额表做借贷平衡自检。小程序端在「管理台」里，网页端此前完全没有 —— 这里补齐。
'use strict';

async function loadAccounting() {
  const ac = state.accounting;
  ac.loading = true;
  render();
  // 三张表各自独立取，任何一张失败不影响其它两张显示
  const q = (p) => api(p).catch((e) => ({ __error: e.message }));
  const [tb, inc, bs] = await Promise.all([
    q(`/api/accounting/trial-balance?period=${ac.period}&exclude_closing=true`),
    q(`/api/accounting/income-statement?period=${ac.period}`),
    q(`/api/accounting/balance-sheet?as_of=${ac.period}-31`),
  ]);
  ac.tb = tb.__error ? null : tb;
  ac.tbError = tb.__error || '';
  ac.inc = inc.__error ? null : inc;
  ac.bs = bs.__error ? null : bs;
  try { ac.closings = (await api('/api/accounting/closings')).closings || []; }
  catch (_) { ac.closings = []; }
  ac.loading = false;
  render();
}

function onAccountingPeriod(v) {
  if (!v) return;
  state.accounting.period = v;
  loadAccounting();
}

async function closePeriod() {
  const p = state.accounting.period;
  if (!confirm(`把 ${p} 这个月的收入/费用汇总成「本月盈亏」？\n汇总后本月那栏会归零，弄错了可以撤销重算。`)) return;
  try {
    const r = await api('/api/accounting/close', 'POST', { period: p });
    toast(`已入账，本月净赚 ${fmt(r.net_profit)} 元` + (r.reclosed ? '（已撤销旧的重新算）' : ''));
    await loadAccounting();
  } catch (e) { toast(e.message); }
}

async function reopenPeriod() {
  const p = state.accounting.period;
  if (!confirm(`撤销 ${p} 的盈亏入账？`)) return;
  try {
    await api('/api/accounting/reopen', 'POST', { period: p });
    toast('已撤销，可重新入账');
    await loadAccounting();
  } catch (e) { toast(e.message); }
}

// ---------------- 渲染 ----------------
function _acRows(rows, labelKey) {
  return rows.map(r => `
    <div class="txn-row">
      <div class="txn-main"><div class="txn-item">${esc(r[labelKey])}</div></div>
      <div class="txn-amount">${fmt(r.amount)}</div>
    </div>`).join('');
}

function renderAccounting() {
  const ac = state.accounting;
  const inc = ac.inc, bs = ac.bs, tb = ac.tb;

  const revRows = inc ? Object.entries(inc.revenue).map(([k, v]) => `
    <div class="txn-row"><div class="txn-main"><div class="txn-item">${esc(k)}</div></div>
      <div class="txn-amount income">${fmt(v)}</div></div>`).join('') : '';
  const expRows = inc ? Object.entries(inc.expense).map(([k, v]) => `
    <div class="txn-row"><div class="txn-main"><div class="txn-item">${esc(k)}</div></div>
      <div class="txn-amount expense">${fmt(v)}</div></div>`).join('') : '';

  const balBadge = (ok, yes, no) =>
    `<div class="acct-badge ${ok ? 'ok' : 'bad'}">${ok ? '✅ ' + yes : '⚠️ ' + no}</div>`;

  return `
  <div class="hero"><div class="hero-title">报表</div>
    <div class="hero-sub">赚了多少钱 · 家底有多少 · 科目对照 · 月底入账</div></div>

  <div class="card">
    <div class="form-item"><label class="form-label">看哪个月</label>
      <input class="form-input" type="month" value="${esc(ac.period)}"
             onchange="onAccountingPeriod(this.value)" /></div>
    <div class="pay-actions">
      <button class="btn-mini" onclick="closePeriod()">把本月盈亏入账 ${esc(ac.period)}</button>
      <button class="btn-mini btn-danger" onclick="reopenPeriod()">撤销入账</button>
    </div>
    <div class="acct-note">入账后，本月收入/费用会汇总到「今年累计赚的」，本月那栏归零；弄错了可以撤销重算。已经入账的月份：
      ${ac.closings && ac.closings.length
        ? ac.closings.map(c => `${esc(c.period)}（净赚 ${fmt(c.net_profit)}${c.status === 'reopened' ? '，已撤销' : ''}）`).join('、')
        : '无'}</div>
  </div>

  ${ac.loading ? '<div class="card"><div class="card-title">加载中…</div></div>' : ''}

  ${inc ? `
  <div class="card">
    <div class="card-title">📈 赚了多少钱（${esc(inc.period)}）</div>
    ${revRows || '<div class="empty">本期没有收入</div>'}
    <div class="txn-row acct-total"><div class="txn-main"><div class="txn-item">收入一共</div></div>
      <div class="txn-amount income">${fmt(inc.total_revenue)}</div></div>
    ${expRows || '<div class="empty">本期没有成本费用</div>'}
    <div class="txn-row acct-total"><div class="txn-main"><div class="txn-item">成本费用一共</div></div>
      <div class="txn-amount expense">${fmt(inc.total_expense)}</div></div>
    <div class="txn-row acct-total acct-net"><div class="txn-main"><div class="txn-item">净赚</div></div>
      <div class="txn-amount">${fmt(inc.net_profit)}</div></div>
    <div class="acct-note">${esc(inc.note || '')}</div>
  </div>` : ''}

  ${bs ? `
  <div class="card">
    <div class="card-title">🏦 家底表（资产负债表，截至 ${esc(bs.as_of)}）</div>
    <div class="acct-sub">手里的/能变现的</div>
    ${_acRows(bs.assets, 'account_name') || '<div class="empty">暂无资产科目</div>'}
    <div class="txn-row acct-total"><div class="txn-main"><div class="txn-item">资产一共</div></div>
      <div class="txn-amount">${fmt(bs.total_assets)}</div></div>
    <div class="acct-sub">欠别人的</div>
    ${_acRows(bs.liabilities, 'account_name') || '<div class="empty">暂无负债</div>'}
    <div class="txn-row acct-total"><div class="txn-main"><div class="txn-item">欠款一共</div></div>
      <div class="txn-amount">${fmt(bs.total_liabilities)}</div></div>
    <div class="acct-sub">真正属于自己的</div>
    ${_acRows(bs.equity, 'account_name') || '<div class="empty">暂无权益</div>'}
    <div class="txn-row acct-total"><div class="txn-main"><div class="txn-item">欠款 + 自己的</div></div>
      <div class="txn-amount">${fmt(bs.liabilities_and_equity)}</div></div>
    ${balBadge(bs.balanced, '对上了（已平衡）：资产 = 欠款 + 自己的', '没对上，请检查凭证')}
    <div class="acct-note">${esc(bs.note || '')}</div>
  </div>` : ''}

  ${tb ? `
  <div class="card">
    <div class="card-title">📋 科目进出对照（${esc(tb.period)}）</div>
    ${balBadge(tb.balanced,
      `借贷对上了（借 ${fmt(tb.total_debit)} / 贷 ${fmt(tb.total_credit)}）`,
      '借贷没对上，请检查凭证')}
    ${tb.lines.map(l => `
      <div class="txn-row">
        <div class="txn-main">
          <div class="txn-item">${esc(l.account_name)}</div>
          <div class="txn-sub">${esc(l.account_code)} · ${esc(l.category_name || '')}</div>
        </div>
        <div class="txn-amount">借 ${fmt(l.debit)} / 贷 ${fmt(l.credit)}</div>
      </div>`).join('')}
    <div class="acct-note">${esc(tb.note || '')}</div>
  </div>` : ''}

  ${ac.tbError ? `<div class="card"><div class="acct-note">科目对照加载失败：${esc(ac.tbError)}</div></div>` : ''}`;
}
