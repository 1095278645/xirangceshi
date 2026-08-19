// 账本页：流水 / 算税 / 科目 / 报表（依赖 core.js 的 state/api/toast/render/pad2/fmt）
'use strict';

// ---------- 账本（省账通能力） ----------
async function loadBooks() {
  const now = new Date();
  if (!state.books.year) {
    state.books.year = now.getFullYear();
    state.books.month = now.getMonth() + 1;
  }
  await loadTxnList();
  try {
    if (state.books.accountCats.length === 0) {
      const r = await api('/api/account-titles');
      state.books.accountCats = r.categories || [];
    }
  } catch (_) {}
  render();
}

async function loadTxnList() {
  const { year, month } = state.books;
  try {
    state.books.txns = await api(`/api/transactions?year=${year}&month=${month}`);
  } catch (_) { state.books.txns = []; }
  render();
  loadInsights();
}

async function loadInsights() {
  const { year, month } = state.books;
  if (!year || !month) return;
  state.books.insightLoading = true;
  state.books.insight = null;
  render();
  try {
    const r = await api('/api/orders/insights', 'POST', { year, month });
    state.books.insight = r.insights;
    state.books.insightAiUsed = r.ai_used;
  } catch (_) { state.books.insight = null; }
  state.books.insightLoading = false;
  render();
}

function switchBookTab(i) {
  state.books.tab = i;
  if (i === 1 && !state.books.calendar) loadCalendar();
  if (i === 0) loadTxnList();
  render();
}

function setBooksMonth(v) {
  const [y, m] = v.split('-').map(Number);
  state.books.year = y;
  state.books.month = m;
  loadTxnList();
}

async function loadCalendar() {
  try {
    state.books.calendar = await api(`/api/tax/calendar?year=${state.books.year}&month=${state.books.month}`);
  } catch (_) {}
  render();
}

async function calcVat() {
  const v = parseFloat(state.books.vatRevenue);
  if (!v || v <= 0) { toast('先填季度销售额'); return; }
  try {
    const r = await api('/api/tax/vat', 'POST', { quarterly_revenue: v });
    state.books.vatResult = r;
    state.books.surtaxResult = r.vat > 0 ? await api('/api/tax/surtax', 'POST', { vat: r.vat }) : null;
  } catch (e) { toast(e.message); }
  render();
  loadTaxAdvice();
}

async function loadTaxAdvice() {
  const v = parseFloat(state.books.vatRevenue);
  if (!v || v <= 0) return;
  state.books.taxAdviceLoading = true;
  state.books.taxAdvice = null;
  render();
  try {
    const r = await api('/api/tax/advice', 'POST', { quarterly_revenue: v });
    state.books.taxAdvice = r.advice;
    state.books.taxAdviceAiUsed = r.ai_used;
  } catch (_) { state.books.taxAdvice = null; }
  state.books.taxAdviceLoading = false;
  render();
}

async function calcPit() {
  const salary = parseFloat(state.books.pitSalary);
  if (!salary || salary <= 0) { toast('先填月工资'); return; }
  try {
    state.books.pitResult = await api('/api/tax/pit', 'POST', {
      salary,
      social_insurance: parseFloat(state.books.pitSocial) || 0,
      special_deduction: parseFloat(state.books.pitSpecial) || 0
    });
  } catch (e) { toast(e.message); }
  render();
}

async function calcCit() {
  const income = parseFloat(state.books.citIncome);
  if (!income || income <= 0) { toast('先填年应纳税所得额'); return; }
  try {
    state.books.citResult = await api('/api/tax/cit', 'POST', { annual_income: income, is_small: state.books.citSmall });
  } catch (e) { toast(e.message); }
  render();
}

// ---------- 渲染 ----------
function renderBooks() {
  const b = state.books;
  // 首次渲染时 year/month 可能尚未由 loadBooks 异步初始化，回退到当前年月
  const nowY = new Date().getFullYear();
  const nowM = new Date().getMonth() + 1;
  const y = b.year || nowY;
  const m = b.month || nowM;
  const tab = ['流水', '算税', '科目', '报表'];
  const monthVal = `${y}-${pad2(m)}`;
  const seg = tab.map((t, i) =>
    `<div class="seg-item ${b.tab === i ? 'active' : ''}" onclick="switchBookTab(${i})">${t}</div>`).join('');

  let body = '';
  if (b.tab === 0) {
    body = `
    <div class="card">
      <div class="card-title">📅 ${y}年${m}月流水
        <input type="month" value="${monthVal}" class="month-input" onchange="setBooksMonth(this.value)" />
      </div>
      ${b.txns.length === 0 ? '<div class="empty">本月还没有记账，去首页说一笔吧</div>' : b.txns.map(t => `
      <div class="txn-row">
        <div class="txn-main">
          <div class="txn-item">${t.item}</div>
          <div class="txn-sub">${t.customer_name || '散客'} · ${t.friendly}${t.counterparty ? ' · ' + t.counterparty : ''}</div>
          <div class="txn-time">${t.created_at || ''}</div>
        </div>
        <div class="txn-amount ${t.trans_type === 'income' ? 'income' : 'expense'}">${t.trans_type === 'income' ? '+' : '-'}${t.amount}</div>
      </div>`).join('')}
    </div>
    ${b.insightLoading ? '<div class="card"><div class="card-title">📊 经营洞察</div><div class="empty">分析中…</div></div>' : ''}
    ${b.insight ? `<div class="card"><div class="card-title">📊 经营洞察 ${b.insightAiUsed ? '✨' : '📝'}</div><div class="review-box">${b.insight}</div></div>` : ''}`;
  } else if (b.tab === 1) {
    body = `
    <div class="card">
      <div class="card-title">增值税（小规模）</div>
      <div class="form-item"><label class="form-label">季度销售额</label>
        <input class="form-input" type="number" placeholder="如 350000" value="${b.vatRevenue}" oninput="state.books.vatRevenue=this.value" /></div>
      <button class="btn-primary" onclick="calcVat()">计算</button>
      ${b.vatResult ? `
      <div class="result-box">
        <div>应缴增值税：<span class="num ${b.vatResult.vat === 0 ? 'zero' : ''}">${b.vatResult.vat} 元</span></div>
        <div class="note">${b.vatResult.note}</div>
      </div>` : ''}
      ${b.surtaxResult ? `
      <div class="result-box">
        <div>附加税合计：<span class="num">${b.surtaxResult.total} 元</span></div>
        ${b.surtaxResult.items.map(i => `<div class="note">${i.name}：${i.amount} 元</div>`).join('')}
        ${b.surtaxResult.six_tax_relief ? '<div class="note">已享受六税两费减半</div>' : ''}
      </div>` : ''}
    </div>
    ${b.taxAdviceLoading || b.taxAdvice ? `
    <div class="card">
      <div class="card-title">${b.taxAdviceAiUsed ? '✨ AI' : '📝 基础'}报税建议</div>
      ${b.taxAdviceLoading ? '<div class="empty">生成中…</div>' : `<div class="review-box">${b.taxAdvice}</div>`}
    </div>` : ''}
    <div class="card">
      <div class="card-title">个人所得税（工资薪金）</div>
      <div class="form-item"><label class="form-label">月工资</label>
        <input class="form-input" type="number" placeholder="如 15000" value="${b.pitSalary}" oninput="state.books.pitSalary=this.value" /></div>
      <div class="form-item"><label class="form-label">社保/公积金</label>
        <input class="form-input" type="number" placeholder="0" value="${b.pitSocial}" oninput="state.books.pitSocial=this.value" /></div>
      <div class="form-item"><label class="form-label">专项附加扣除</label>
        <input class="form-input" type="number" placeholder="0" value="${b.pitSpecial}" oninput="state.books.pitSpecial=this.value" /></div>
      <button class="btn-primary" onclick="calcPit()">计算</button>
      ${b.pitResult ? `
      <div class="result-box">
        <div>应纳税所得额：<span class="num">${b.pitResult.taxable} 元</span></div>
        <div>应缴个税：<span class="num">${b.pitResult.tax} 元</span></div>
        <div class="note">税率 ${(b.pitResult.rate * 100).toFixed(0)}%（速算扣除 ${b.pitResult.quick_deduction}）</div>
      </div>` : ''}
    </div>
    <div class="card">
      <div class="card-title">企业所得税</div>
      <div class="form-item"><label class="form-label">年应纳税所得额</label>
        <input class="form-input" type="number" placeholder="如 2500000" value="${b.citIncome}" oninput="state.books.citIncome=this.value" /></div>
      <div class="form-item">
        <label class="radio-inline"><input type="radio" name="citSmall" ${b.citSmall ? 'checked' : ''} onchange="state.books.citSmall=true;render()" />小微企业</label>
        <label class="radio-inline"><input type="radio" name="citSmall" ${!b.citSmall ? 'checked' : ''} onchange="state.books.citSmall=false;render()" />一般企业</label>
      </div>
      <button class="btn-primary" onclick="calcCit()">计算</button>
      ${b.citResult ? `
      <div class="result-box">
        ${(b.citResult.details || []).map(d => `<div class="note">${d.range} 元段 × ${(d.rate * 100).toFixed(0)}% = ${d.tax} 元</div>`).join('')}
        <div>应缴企业所得税：<span class="num">${b.citResult.total_tax || b.citResult.tax} 元</span></div>
        <div class="note">${b.citResult.note}</div>
      </div>` : ''}
    </div>
    ${b.calendar ? `
    <div class="card">
      <div class="card-title">📅 ${b.calendar.year}年${b.calendar.month}月报税提醒</div>
      ${b.calendar.reminders.map(r => `
      <div class="result-box">
        <div><span class="num">${r.tax_type}</span>：${r.deadline} 前</div>
        <div class="note">${r.note}</div>
      </div>`).join('')}
    </div>` : ''}`;
  } else if (b.tab === 2) {
    body = b.accountCats.map(c => `
    <div class="card">
      <div class="card-title">${c.name}</div>
      ${c.titles.map(t => `
      <div class="cat-row"><span class="cat-code">${t.code}</span><span class="cat-name">${t.name}</span></div>`).join('')}
    </div>`).join('');
  } else {
    body = `
    <div class="card">
      <div class="card-title">导出 Excel 报表</div>
      <div class="form-item"><label class="form-label">报表月份</label>
        <input type="month" value="${monthVal}" class="form-input" onchange="setBooksMonth(this.value)" /></div>
      <a class="btn-primary btn-link" href="/api/report/monthly?year=${y}&month=${m}" download>📥 下载 ${y}年${m}月报表（xlsx）</a>
      <div class="note">报表含：收支汇总、分类明细、交易流水 三个工作表</div>
    </div>`;
  }

  return `
  <div class="hero"><div class="hero-title">账本</div><div class="hero-sub">查账 · 算税 · 科目 · 报表，全在这一本</div></div>
  <div class="seg">${seg}</div>
  ${body}`;
}