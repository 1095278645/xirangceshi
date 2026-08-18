// 巷子里的AI掌柜 · 网页端逻辑（单页应用 + Web Speech API 语音记账）
'use strict';

// ---------- API 封装（同源，直接 fetch） ----------
async function api(path, method = 'GET', data = null) {
  const opt = { method, headers: { 'Content-Type': 'application/json' } };
  if (data) opt.body = JSON.stringify(data);
  let res;
  try {
    res = await fetch(path, opt);
  } catch (e) {
    throw new Error('无法连接小店服务，请确认后端已启动');
  }
  if (!res.ok) {
    let msg = '请求失败 ' + res.status;
    try { const e = await res.json(); msg = e.detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

// ---------- 全局状态 ----------
const state = {
  route: 'home',
  summary: { income: 0, expense: 0, balance: 0, cnt: 0 },
  month: { period: '', income: 0, expense: 0, balance: 0 },
  recognizing: false,
  result: '',
  submitting: false,
  parsed: null,
  voucher: null,
  friendlyCategory: '',
  manualText: '',
  voiceSupported: false,
  // 熟客
  customers: [],
  custDetail: null,
  custMemInput: '',
  // 文案
  copyForm: { shop_name: '巷子里的早餐铺', scene: '今日营业', extra: '', customer_name: '' },
  copyResult: '',
  // 设置
  aiEnabled: false,
  hasKey: false,
  baseUrl: '',
  model: '',
  provider: '',
  providers: [],
  apiKeyInput: '',
  baseUrlInput: '',
  modelInput: '',
  // 收款账户（微信商户/聚合支付流水同步）
  paySources: [],
  payLogs: [],
  payForm: { source_type: 'wechat', name: '', mchid: '', appid: '',
    cert_path: '', private_key_path: '', api_v3_key: '', enabled: true },
  paySyncing: false,
  // 账本（省账通能力）
  books: { tab: 0, year: 0, month: 0, txns: [], summaryLoading: false,
    vatRevenue: '', vatResult: null, surtaxResult: null,
    pitSalary: '', pitSocial: '', pitSpecial: '', pitResult: null,
    citIncome: '', citSmall: true, citResult: null,
    calendar: null, accountCats: [], downloading: false },
  // 单店模型（勇哥方法论泛化）
  store: { presets: [], bizType: '餐饮', form: { daily_revenue: '', gross_margin: '',
    rent: '', salary: '', utilities: '', total_investment: '', cash_on_hand: '',
    traffic: '一般', competitor: '一般' },
    result: null, loading: false },
};

let recognition = null;

// ---------- 语音识别（Web Speech API） ----------
function initSpeech() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    state.voiceSupported = false;
    return;
  }
  state.voiceSupported = true;
  recognition = new SR();
  recognition.lang = 'zh-CN';
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.onresult = (e) => {
    let txt = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      txt += e.results[i][0].transcript;
    }
    state.result = txt;
    render();
  };
  recognition.onend = () => {
    state.recognizing = false;
    if (state.result) {
      submitOrder(state.result);
    }
    render();
  };
  recognition.onerror = () => {
    state.recognizing = false;
    toast('语音不可用（非 HTTPS/localhost 下浏览器可能禁用），请手动输入');
    render();
  };
}

function startRecord() {
  if (!recognition) {
    toast('当前浏览器不支持语音，请手动输入');
    return;
  }
  state.result = '';
  state.parsed = null;
  try {
    recognition.start();
    state.recognizing = true;
    render();
  } catch (e) {
    toast('语音启动失败，请手动输入');
  }
}

function stopRecord() {
  if (recognition && state.recognizing) recognition.stop();
}

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
    const [s, m] = await Promise.all([api('/api/orders/today'), api('/api/orders/monthly')]);
    state.summary = s;
    state.month = m;
  } catch (_) {}
  render();
}

async function loadMonth() {
  try { state.month = await api('/api/orders/monthly'); } catch (_) {}
  render();
}

// ---------- 熟客 ----------
async function loadCustomers() {
  try { state.customers = await api('/api/customers'); } catch (_) {}
  render();
}

async function viewCustomer(id) {
  try {
    state.custDetail = await api('/api/customers/' + id);
    state.route = 'custDetail';
    render();
  } catch (e) { toast(e.message); }
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

// ---------- 文案 ----------
async function generateCopy() {
  state.copyResult = '';
  render();
  try {
    const r = await api('/api/copy', 'POST', state.copyForm);
    state.copyResult = r.text;
  } catch (e) { toast(e.message); }
  render();
}

// ---------- 设置 ----------
async function loadSettings() {
  try {
    const [s, p, ps, pl] = await Promise.all([
      api('/api/settings'), api('/api/providers'),
      api('/api/payment/sources'), api('/api/payment/logs'),
    ]);
    state.aiEnabled = s.ai_enabled;
    state.hasKey = s.has_key;
    state.baseUrl = s.base_url;
    state.model = s.model;
    state.provider = s.provider || 'custom';
    state.providers = p.providers || [];
    state.baseUrlInput = s.base_url;
    state.modelInput = s.model;
    state.paySources = ps.sources || [];
    state.payLogs = pl.logs || [];
  } catch (_) {}
  render();
}

async function saveSettings() {
  const key = state.apiKeyInput.trim();
  if (!key) { toast('请先粘贴 API Key'); return; }
  try {
    const r = await api('/api/settings', 'POST', {
      api_key: key,
      base_url: state.baseUrlInput.trim(),
      model: state.modelInput.trim()
    });
    state.aiEnabled = r.ai_enabled;
    state.baseUrl = r.base_url;
    state.model = r.model;
    state.apiKeyInput = '';
    toast('已保存，AI 生效');
  } catch (e) { toast(e.message); }
  render();
}

async function clearKey() {
  if (!confirm('清除后将回到兜底模式，确定？')) return;
  try {
    const r = await api('/api/settings', 'POST', { api_key: '' });
    state.aiEnabled = r.ai_enabled;
    state.baseUrl = r.base_url;
    state.model = r.model;
    toast('已清除');
  } catch (e) { toast(e.message); }
  render();
}

function selectProvider(id) {
  state.provider = id;
  const p = state.providers.find(x => x.id === id);
  if (p) {
    if (p.base_url) state.baseUrlInput = p.base_url;
    if (p.model) state.modelInput = p.model;
  }
  render();
}

// ---------- 收款账户（二维码收付款流水同步） ----------
function setPayType(t) {
  state.payForm.source_type = t;
  render();
}

function payFormEnabled(v) {
  state.payForm.enabled = v;
  render();
}

async function savePaySource(sid) {
  const f = state.payForm;
  if (!f.name.trim()) { toast('请填写账户名称'); return; }
  if (!f.mchid.trim()) { toast('请填写商户号（无商户资料可填 DEMO 体验）'); return; }
  try {
    await api('/api/payment/sources', 'POST', {
      sid: sid || null, source_type: f.source_type, name: f.name.trim(),
      mchid: f.mchid.trim(), appid: f.appid.trim(),
      cert_path: f.cert_path.trim(), private_key_path: f.private_key_path.trim(),
      api_v3_key: f.api_v3_key.trim(), enabled: f.enabled,
    });
    state.payForm = { source_type: 'wechat', name: '', mchid: '', appid: '',
      cert_path: '', private_key_path: '', api_v3_key: '', enabled: true };
    toast('已保存');
    await loadSettings();
  } catch (e) { toast(e.message); }
}

async function deletePaySource(sid, name) {
  if (!confirm(`删除收款账户「${name}」？已同步的流水不受影响。`)) return;
  try {
    await api('/api/payment/sources/' + sid, 'DELETE');
    toast('已删除');
    await loadSettings();
  } catch (e) { toast(e.message); }
}

async function syncPaySource(sid) {
  state.paySyncing = true;
  render();
  try {
    const r = await api('/api/payment/sources/' + sid + '/sync', 'POST');
    if (r.ok) toast(`同步完成：新增 ${r.imported} 笔`);
    else toast('同步失败：' + r.error);
  } catch (e) { toast(e.message); }
  state.paySyncing = false;
  await loadSettings();
}

async function syncAllPay() {
  state.paySyncing = true;
  render();
  try {
    const r = await api('/api/payment/sync-all', 'POST');
    toast(`已触发全部账户同步（${r.length} 个结果）`);
  } catch (e) { toast(e.message); }
  state.paySyncing = false;
  await loadSettings();
}

async function clearDemoPay() {
  if (!confirm('将清空所有演示模式（DEMO-）产生的流水，确定？')) return;
  try {
    const r = await api('/api/payment/demo-clear', 'POST');
    toast(`已清空 ${r.deleted} 条演示流水`);
    await loadSettings();
  } catch (e) { toast(e.message); }
}

// ---------- 账本（省账通能力） ----------
function pad2(n) { return n < 10 ? '0' + n : '' + n; }

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

function renderBooks() {
  const b = state.books;
  const tab = ['流水', '算税', '科目', '报表'];
  const monthVal = `${b.year}-${pad2(b.month)}`;
  const seg = tab.map((t, i) =>
    `<div class="seg-item ${b.tab === i ? 'active' : ''}" onclick="switchBookTab(${i})">${t}</div>`).join('');

  let body = '';
  if (b.tab === 0) {
    body = `
    <div class="card">
      <div class="card-title">📅 ${b.year}年${b.month}月流水
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
    </div>`;
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
      <a class="btn-primary btn-link" href="/api/report/monthly?year=${b.year}&month=${b.month}" download>📥 下载 ${b.year}年${b.month}月报表（xlsx）</a>
      <div class="note">报表含：收支汇总、分类明细、交易流水 三个工作表</div>
    </div>`;
  }

  return `
  <div class="hero"><div class="hero-title">账本</div><div class="hero-sub">查账 · 算税 · 科目 · 报表，全在这一本</div></div>
  <div class="seg">${seg}</div>
  ${body}`;
}

// ---------- 单店模型（勇哥方法论泛化：保本线先行） ----------
async function loadStore() {
  try {
    const r = await api('/api/store/presets');
    state.store.presets = r.presets || [];
  } catch (_) {}
  render();
}

function setStoreBiz(t) {
  state.store.bizType = t;
  state.store.form.gross_margin = '';  // 切换类型后让用户用业态默认毛利率
  state.store.result = null;
  render();
}

async function calcStoreModel() {
  const f = state.store.form;
  const nums = [f.daily_revenue, f.rent, f.salary, f.utilities, f.total_investment, f.cash_on_hand];
  if (nums.every(v => !String(v).trim())) { toast('至少填一项数据，让掌柜帮你算'); return; }
  state.store.loading = true;
  state.store.result = null;
  render();
  try {
    const body = {
      daily_revenue: parseFloat(f.daily_revenue) || 0,
      gross_margin: f.gross_margin ? (parseFloat(f.gross_margin) / 100) : null,
      rent: parseFloat(f.rent) || 0,
      salary: parseFloat(f.salary) || 0,
      utilities: parseFloat(f.utilities) || 0,
      total_investment: parseFloat(f.total_investment) || 0,
      cash_on_hand: parseFloat(f.cash_on_hand) || 0,
      traffic: f.traffic,
      competitor: f.competitor,
      biz_type: state.store.bizType,
    };
    state.store.result = await api('/api/store/model', 'POST', body);
  } catch (e) {
    toast(e.message);
  }
  state.store.loading = false;
  render();
}

function renderStore() {
  const s = state.store;
  const f = s.form;
  const preset = s.presets.find(p => p.key === s.bizType) || {};
  const marginHint = preset.margin_default
    ? `参考毛利率 ${(preset.margin_range[0] * 100).toFixed(0)}%-${(preset.margin_range[1] * 100).toFixed(0)}%（不填自动用默认 ${(preset.margin_default * 100).toFixed(0)}%）`
    : '';
  const presetOpts = s.presets.map(p => `<option value="${p.key}" ${p.key === s.bizType ? 'selected' : ''}>${p.name}</option>`).join('');

  let resultHtml = '';
  if (s.result) {
    const r = s.result;
    const m = r.model;
    const dim = r.dimensions;
    const levelIcon = { ok: '🟢', warn: '🟡', danger: '🔴' }[r.overall.key] || '⚪';
    resultHtml = `
    <div class="card">
      <div class="card-title">诊断结论：${levelIcon} ${r.overall.level}
        <span class="store-score">综合分 ${r.overall.score}</span></div>
      <div class="result-box">
        <div class="store-verdict">${r.advice}</div>
      </div>
      ${r.cash_flags.length ? r.cash_flags.map(cf => `<div class="result-box warn-box">${cf}</div>`).join('') : ''}
      <div class="note">${r.biz_rule}</div>
    </div>
    <div class="card">
      <div class="card-title">保本线（这是你店的命线）</div>
      <div class="store-grid">
        <div class="store-cell"><div class="store-cell-num">${fmt(r.model.break_even_day)}</div><div class="store-cell-label">保本日销（元/天）</div></div>
        <div class="store-cell"><div class="store-cell-num">${fmt(r.model.target_day)}</div><div class="store-cell-label">目标日销 ×1.3（元/天）</div></div>
        <div class="store-cell"><div class="store-cell-num">${fmt(r.model.fixed_month)}</div><div class="store-cell-label">月固定支出（元）</div></div>
        <div class="store-cell"><div class="store-cell-num">${r.model.payback_months == null ? '∞' : r.model.payback_months}</div><div class="store-cell-label">回本周期（月）</div></div>
      </div>
      <div class="note">月毛利${fmt(r.model.month_revenue * r.inputs.gross_margin)}元 − 固定支出${fmt(r.model.fixed_month)}元 = 月利润${fmt(r.model.month_profit)}元</div>
      <div class="note">现金流：现有现金可扛 ${r.model.cash_months == null ? '∞' : r.model.cash_months} 个月</div>
    </div>
    <div class="card">
      <div class="card-title">三维交叉验证</div>
      ${['a', 'b', 'c'].map(k => {
        const d2 = dim[k];
        const ic = { '健康': '🟢', '临界': '🟡', '危险': '🔴' }[d2.level] || '⚪';
        return `<div class="dim-row"><span class="dim-name">${ic} ${d2.name}</span><span class="dim-level ${d2.level}">${d2.level}</span><span class="dim-score">${d2.score}分</span></div>`;
      }).join('')}
    </div>`;
  }

  return `
  <div class="hero"><div class="hero-title">单店模型</div><div class="hero-sub">保本线先行，赚不赚钱心里有数</div></div>
  <div class="card">
    <div class="card-title">你的店，如实填</div>
    <div class="form-item">
      <label class="form-label">业态</label>
      <select class="form-select" onchange="setStoreBiz(this.value)">${presetOpts}</select>
      <div class="note">${preset ? preset.note : ''}</div>
    </div>
    <div class="form-item">
      <label class="form-label">实际日营业额（元）</label>
      <input class="form-input" type="number" placeholder="如 1200" value="${f.daily_revenue}" oninput="state.store.form.daily_revenue=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">毛利率 %（可不填）</label>
      <input class="form-input" type="number" placeholder="${marginHint}" value="${f.gross_margin}" oninput="state.store.form.gross_margin=this.value" />
      <div class="note">${marginHint}</div>
    </div>
    <div class="form-item">
      <label class="form-label">月房租（元）</label>
      <input class="form-input" type="number" placeholder="如 6000" value="${f.rent}" oninput="state.store.form.rent=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">月人工（元，含自己）</label>
      <input class="form-input" type="number" placeholder="如 8000" value="${f.salary}" oninput="state.store.form.salary=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">月水电杂费（元）</label>
      <input class="form-input" type="number" placeholder="如 2000" value="${f.utilities}" oninput="state.store.form.utilities=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">总投资（含转让/装修/设备，元）</label>
      <input class="form-input" type="number" placeholder="如 200000" value="${f.total_investment}" oninput="state.store.form.total_investment=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">现有现金（元）</label>
      <input class="form-input" type="number" placeholder="如 50000" value="${f.cash_on_hand}" oninput="state.store.form.cash_on_hand=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">商圈客流</label>
      <select class="form-select" onchange="state.store.form.traffic=this.value;render()">
        <option value="差" ${f.traffic === '差' ? 'selected' : ''}>差（人少）</option>
        <option value="一般" ${f.traffic === '一般' ? 'selected' : ''}>一般</option>
        <option value="好" ${f.traffic === '好' ? 'selected' : ''}>好（人流旺）</option>
      </select>
    </div>
    <div class="form-item">
      <label class="form-label">周边竞争</label>
      <select class="form-select" onchange="state.store.form.competitor=this.value;render()">
        <option value="多" ${f.competitor === '多' ? 'selected' : ''}>多（竞品扎堆）</option>
        <option value="一般" ${f.competitor === '一般' ? 'selected' : ''}>一般</option>
        <option value="少" ${f.competitor === '少' ? 'selected' : ''}>少</option>
      </select>
    </div>
    <button class="btn-primary ${s.loading ? 'disabled' : ''}" onclick="calcStoreModel()">${s.loading ? '算账中…' : '算账'}</button>
  </div>
  ${resultHtml}`;
}

// ---------- 渲染 ----------
function fmt(n) { return Number(n || 0).toFixed(0); }

function renderHome() {
  const p = state.parsed;
  return `
  <div class="hero">
    <div class="hero-title">老板，今天辛苦啦！</div>
    <div class="hero-sub">巷子里的早餐铺 · AI掌柜已就位</div>
  </div>

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

function renderCopy() {
  return `
  <div class="hero"><div class="hero-title">朋友圈文案</div><div class="hero-sub">老板语气，烟火气，随手发</div></div>
  <div class="card">
    <div class="form-item">
      <label class="form-label">店名</label>
      <input class="form-input" value="${state.copyForm.shop_name}" oninput="state.copyForm.shop_name=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">场景</label>
      <input class="form-input" value="${state.copyForm.scene}" oninput="state.copyForm.scene=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">补充信息</label>
      <textarea class="form-textarea" placeholder="比如：新到了土鸡蛋、今天包子买一送一"
        oninput="state.copyForm.extra=this.value">${state.copyForm.extra}</textarea>
    </div>
    <div class="form-item">
      <label class="form-label">想带的熟客名（可选）</label>
      <input class="form-input" value="${state.copyForm.customer_name}" oninput="state.copyForm.customer_name=this.value" />
    </div>
    <button class="btn-primary" onclick="generateCopy()">生成文案</button>
  </div>
  ${state.copyResult ? `<div class="card"><div class="result-text">${state.copyResult}</div><button class="btn-ghost" onclick="copyText()">复制到剪贴板</button></div>` : ''}`;
}

function renderSettings() {
  const provs = state.providers;
  const curProv = state.providers.find(p => p.id === state.provider) || provs.find(p => p.id === 'custom') || {};
  const keyLabel = curProv.key_label || 'API Key';
  const keyUrl = curProv.key_url || '';
  return `
  <div class="hero"><div class="hero-title">设置</div><div class="hero-sub">AI 模型 · 收款账户 · 数据管理</div></div>
  <div class="card">
    <div class="card-title">AI 服务状态</div>
    <div class="status-row">
      <div class="status-dot ${state.aiEnabled ? 'on' : ''}"></div>
      <span class="status-text">${state.aiEnabled ? '已开启 · AI 生效中' : '未配置 · 兜底模式'}</span>
    </div>
    <div class="status-detail">服务地址：${state.baseUrl}</div>
    <div class="status-detail">模型：${state.model}</div>
    ${state.aiEnabled ? '' : '<div class="status-detail">兜底模式记账/熟客仍可用，智能解析与提醒受限。</div>'}
  </div>
  <div class="card">
    <div class="card-title">AI 模型配置</div>
    <div class="form-item">
      <label class="form-label">选择大模型</label>
      <select class="form-select" onchange="selectProvider(this.value)">
        ${provs.map(p => `<option value="${p.id}" ${p.id === state.provider ? 'selected' : ''}>${p.name}</option>`).join('')}
      </select>
    </div>
    <div class="form-item">
      <label class="form-label">${keyLabel}</label>
      <input class="form-input" type="password" placeholder="粘贴你的 API Key"
        value="${state.apiKeyInput}" oninput="state.apiKeyInput=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">Base URL${state.provider !== 'custom' ? '（已自动填充，可改）' : ''}</label>
      <input class="form-input" value="${state.baseUrlInput}" oninput="state.baseUrlInput=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">模型${state.provider !== 'custom' ? '（已自动填充，可改）' : ''}</label>
      <input class="form-input" value="${state.modelInput}" oninput="state.modelInput=this.value" />
    </div>
    <button class="btn-primary" onclick="saveSettings()">保存并启用</button>
    ${state.hasKey ? '<button class="btn-ghost" onclick="clearKey()">清除 API Key</button>' : ''}
    ${keyUrl
      ? `<span class="link-btn" onclick="copyLink('${keyUrl}')">还没有 Key？去「${curProv.name}」开通（复制链接）</span>`
      : '<div class="howto-text">自定义服务请到对应平台获取 API Key。</div>'}
  </div>
  ${renderPaySettings()}
  <div class="card">
    <div class="card-title">说明</div>
    <div class="howto-text">1. 支持多家大模型，选好后填对应的 API Key 即可。</div>
    <div class="howto-text">2. Key 只保存在你自己的电脑上，不会上传。</div>
    <div class="howto-text">3. 保存后立即生效，无需重启后端。</div>
    <div class="howto-text">4. 手机访问请用电脑局域网 IP，并确保防火墙放行 8000 端口。</div>
  </div>`;
}

function renderPaySettings() {
  const f = state.payForm;
  const srcHtml = state.paySources.length ? state.paySources.map(s => `
    <div class="pay-row">
      <div class="pay-row-main">
        <span class="pay-type-badge">${s.source_type === 'wechat' ? '微信商户' : '聚合支付'}</span>
        <strong>${s.name || '(未命名)'}</strong>
        <span class="pay-mchid">商户号：${s.mchid || '-'}</span>
        <span class="pay-status">${s.enabled ? '✅ 已启用' : '⏸ 未启用'}</span>
      </div>
      <div class="pay-row-actions">
        <button class="btn-mini" onclick="syncPaySource(${s.id})" ${state.paySyncing ? 'disabled' : ''}>同步</button>
        <button class="btn-mini btn-danger" onclick="deletePaySource(${s.id}, '${s.name}')">删除</button>
      </div>
    </div>`).join('') : '<div class="howto-text">还没有收款账户。填商户号 DEMO 即可体验演示模式，无需任何商户资料。</div>';
  const logsHtml = state.payLogs.length ? state.payLogs.slice(0, 5).map(l => `
    <div class="log-row">
      <span class="log-status ${l.status}">${l.status === 'success' ? '✓' : l.status === 'empty' ? '○' : '✗'}</span>
      <span class="log-text">${l.source_name || '账户'}</span>
      <span class="log-text">${l.bill_date}</span>
      <span class="log-text">${l.status === 'error' ? (l.error || '失败').slice(0, 30) : `新增 ${l.imported} 笔`}</span>
    </div>`).join('') : '<div class="howto-text">暂无同步记录。</div>';

  return `
  <div class="card">
    <div class="card-title">收款账户（二维码收付款流水自动入账本）</div>
    <div class="pay-type-toggle">
      <button class="pay-type-btn ${f.source_type === 'wechat' ? 'on' : ''}" onclick="setPayType('wechat')">微信支付商户号</button>
      <button class="pay-type-btn ${f.source_type === 'aggregate' ? 'on' : ''}" onclick="setPayType('aggregate')">聚合支付（无执照）</button>
    </div>
    <div class="form-item">
      <label class="form-label">账户名称</label>
      <input class="form-input" placeholder="如：店里收款码" value="${f.name}" oninput="state.payForm.name=this.value" />
    </div>
    ${f.source_type === 'wechat' ? `
    <div class="form-item">
      <label class="form-label">微信支付商户号（mchid）</label>
      <input class="form-input" placeholder="无商户资料可填 DEMO 体验演示模式" value="${f.mchid}" oninput="state.payForm.mchid=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">AppID（可选）</label>
      <input class="form-input" placeholder="小程序/公众号 AppID" value="${f.appid}" oninput="state.payForm.appid=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">商户 API 证书路径（apiclient_cert.pem）</label>
      <input class="form-input" placeholder="C:\\cert\\apiclient_cert.pem" value="${f.cert_path}" oninput="state.payForm.cert_path=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">商户 API 私钥路径（apiclient_key.pem）</label>
      <input class="form-input" placeholder="C:\\cert\\apiclient_key.pem" value="${f.private_key_path}" oninput="state.payForm.private_key_path=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">APIv3 密钥</label>
      <input class="form-input" type="password" placeholder="在微信支付商户平台设置" value="${f.api_v3_key}" oninput="state.payForm.api_v3_key=this.value" />
    </div>
    <div class="howto-text">需要：有营业执照 → 微信支付商户平台开通「交易账单」权限 → 下载 API 证书。</div>
    ` : `
    <div class="form-item">
      <label class="form-label">聚合支付商户号</label>
      <input class="form-input" placeholder="收钱吧/付呗等服务商商户号" value="${f.mchid}" oninput="state.payForm.mchid=this.value" />
    </div>
    <div class="howto-text">无执照群体可用。聚合通道正在接入服务商（收钱吧/付桥等），当前保存后同步会提示待接入。</div>
    `}
    <div class="form-item">
      <label class="form-label">
        <input type="checkbox" ${f.enabled ? 'checked' : ''} onchange="payFormEnabled(this.checked)" /> 启用自动同步（每 6 小时拉取昨日账单）
      </label>
    </div>
    <button class="btn-primary" onclick="savePaySource()">保存收款账户</button>
  </div>

  <div class="card">
    <div class="card-title">收款账户</div>
    ${srcHtml}
    <div class="pay-actions">
      <button class="btn-mini" onclick="syncAllPay()" ${state.paySyncing ? 'disabled' : ''}>全部同步</button>
      <button class="btn-mini btn-danger" onclick="clearDemoPay()">清空演示流水</button>
    </div>
  </div>

  <div class="card">
    <div class="card-title">同步记录（最近 ${Math.min(state.payLogs.length, 5)} 条）</div>
    ${logsHtml}
  </div>`;
}

function render() {
  const r = state.route;
  let html;
  if (r === 'customers') html = renderCustomers();
  else if (r === 'custDetail') html = renderCustDetail();
  else if (r === 'copy') html = renderCopy();
  else if (r === 'settings') html = renderSettings();
  else if (r === 'books') html = renderBooks();
  else if (r === 'store') html = renderStore();
  else html = renderHome();
  document.getElementById('app').innerHTML = html;
  document.querySelectorAll('.tab-item').forEach(t => {
    t.classList.toggle('active', t.dataset.route === r);
    t.style.display = (r === 'custDetail' && t.dataset.route !== 'customers') ? 'none' : 'flex';
  });
}

// ---------- 路由 ----------
function go(route) {
  state.route = route;
  if (location.hash.slice(1) !== route) location.hash = route;
  render();
  if (route === 'home') loadHome();
  else if (route === 'customers') loadCustomers();
  else if (route === 'settings') loadSettings();
  else if (route === 'books') loadBooks();
  else if (route === 'store') loadStore();
}

// ---------- 辅助 ----------
let toastTimer = null;
function toast(msg) {
  let el = document.querySelector('.toast');
  if (el) el.remove();
  el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 2200);
}

function submitManual() {
  const t = state.manualText.trim();
  if (!t) { toast('说点啥呢'); return; }
  submitOrder(t);
}

function copyText() {
  navigator.clipboard.writeText(state.copyResult).then(() => toast('已复制')).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = state.copyResult; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove(); toast('已复制');
  });
}

function copyLink(url) {
  navigator.clipboard.writeText(url).then(() => toast('链接已复制')).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = url; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove(); toast('链接已复制');
  });
}

// ---------- 初始化 ----------
window.addEventListener('hashchange', () => {
  const r = location.hash.slice(1) || 'home';
  if (r !== state.route) go(r);
});

document.querySelectorAll('.tab-item').forEach(t => {
  t.addEventListener('click', (e) => { e.preventDefault(); go(t.dataset.route); });
});

initSpeech();
const initRoute = location.hash.slice(1) || 'home';
state.route = initRoute;
render();
go(initRoute);