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
  if (!confirm(`把 ${p} 的收入与费用结转到本年利润？\n结转后该期间的损益类科目归零（可反结转重做）。`)) return;
  try {
    const r = await api('/api/accounting/close', 'POST', { period: p });
    toast(`已结转，净利 ${fmt(r.net_profit)} 元` + (r.reclosed ? '（已红冲旧结转重算）' : ''));
    await loadAccounting();
  } catch (e) { toast(e.message); }
}

async function reopenPeriod() {
  const p = state.accounting.period;
  if (!confirm(`撤销 ${p} 的期末结转？`)) return;
  try {
    await api('/api/accounting/reopen', 'POST', { period: p });
    toast('已反结转，可重新结转');
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
  <div class="hero"><div class="hero-title">会计报表</div>
    <div class="hero-sub">利润表 · 资产负债表 · 科目余额表 · 期末结转</div></div>

  <div class="card">
    <div class="form-item"><label class="form-label">会计期间</label>
      <input class="form-input" type="month" value="${esc(ac.period)}"
             onchange="onAccountingPeriod(this.value)" /></div>
    <div class="pay-actions">
      <button class="btn-mini" onclick="closePeriod()">期末结转 ${esc(ac.period)}</button>
      <button class="btn-mini btn-danger" onclick="reopenPeriod()">反结转</button>
    </div>
    <div class="acct-note">结转后损益类科目归零；结转错了可以「反结转」再重做。已结转期间：
      ${ac.closings && ac.closings.length
        ? ac.closings.map(c => `${esc(c.period)}（净利 ${fmt(c.net_profit)}${c.status === 'reopened' ? '，已反结转' : ''}）`).join('、')
        : '无'}</div>
  </div>

  ${ac.loading ? '<div class="card"><div class="card-title">加载中…</div></div>' : ''}

  ${inc ? `
  <div class="card">
    <div class="card-title">📈 利润表（${esc(inc.period)}）</div>
    ${revRows || '<div class="empty">本期没有收入</div>'}
    <div class="txn-row acct-total"><div class="txn-main"><div class="txn-item">收入合计</div></div>
      <div class="txn-amount income">${fmt(inc.total_revenue)}</div></div>
    ${expRows || '<div class="empty">本期没有成本费用</div>'}
    <div class="txn-row acct-total"><div class="txn-main"><div class="txn-item">成本费用合计</div></div>
      <div class="txn-amount expense">${fmt(inc.total_expense)}</div></div>
    <div class="txn-row acct-total acct-net"><div class="txn-main"><div class="txn-item">净利润</div></div>
      <div class="txn-amount">${fmt(inc.net_profit)}</div></div>
    <div class="acct-note">${esc(inc.note || '')}</div>
  </div>` : ''}

  ${bs ? `
  <div class="card">
    <div class="card-title">🏦 资产负债表（截至 ${esc(bs.as_of)}）</div>
    <div class="acct-sub">资产</div>
    ${_acRows(bs.assets, 'account_name') || '<div class="empty">暂无资产科目</div>'}
    <div class="txn-row acct-total"><div class="txn-main"><div class="txn-item">资产合计</div></div>
      <div class="txn-amount">${fmt(bs.total_assets)}</div></div>
    <div class="acct-sub">负债</div>
    ${_acRows(bs.liabilities, 'account_name') || '<div class="empty">暂无负债</div>'}
    <div class="txn-row acct-total"><div class="txn-main"><div class="txn-item">负债合计</div></div>
      <div class="txn-amount">${fmt(bs.total_liabilities)}</div></div>
    <div class="acct-sub">所有者权益</div>
    ${_acRows(bs.equity, 'account_name') || '<div class="empty">暂无权益</div>'}
    <div class="txn-row acct-total"><div class="txn-main"><div class="txn-item">负债 + 权益</div></div>
      <div class="txn-amount">${fmt(bs.liabilities_and_equity)}</div></div>
    ${balBadge(bs.balanced, '资产 = 负债 + 权益，已平衡', '没有平衡，请检查凭证')}
    <div class="acct-note">${esc(bs.note || '')}</div>
  </div>` : ''}

  ${tb ? `
  <div class="card">
    <div class="card-title">📋 科目余额表（${esc(tb.period)}）</div>
    ${balBadge(tb.balanced,
      `借贷平衡（借 ${fmt(tb.total_debit)} / 贷 ${fmt(tb.total_credit)}）`,
      '借贷不平，请检查凭证')}
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

  ${ac.tbError ? `<div class="card"><div class="acct-note">科目余额表加载失败：${esc(ac.tbError)}</div></div>` : ''}`;
}
