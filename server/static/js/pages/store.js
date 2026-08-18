// 单店模型页（勇哥方法论泛化：保本线先行；依赖 core.js 的 state/api/toast/render/fmt）
'use strict';

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

async function loadStoreLedger() {
  state.store.ledgering = true;
  state.store.ledgerNote = '';
  render();
  try {
    const r = await api('/api/store/from-ledger');
    if (!r.daily_revenue && r.daily_revenue !== 0) {
      toast('账本里还没有收入流水，先去记几笔吧');
      state.store.ledgering = false;
      render();
      return;
    }
    state.store.form.daily_revenue = r.daily_revenue;
    state.store.form.gross_margin = r.gross_margin != null ? (r.gross_margin * 100).toFixed(0) : '';
    state.store.ledgerNote = r.note;
    state.store.result = null;
    toast('已从账本带入');
  } catch (e) {
    toast(e.message);
  }
  state.store.ledgering = false;
  render();
}

// ---------- 渲染 ----------
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
    ${s.ledgerNote ? `<div class="result-box ledger-box">📖 ${s.ledgerNote}</div>` : ''}
    <button class="btn-secondary ${s.ledgering ? 'disabled' : ''}" onclick="loadStoreLedger()">${s.ledgering ? '读取中…' : '📖 从账本流水带入'}</button>
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