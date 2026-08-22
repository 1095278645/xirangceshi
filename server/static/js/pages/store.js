// 单店模型页（勇哥方法论泛化：保本线先行；依赖 core.js 的 state/api/toast/render/fmt）
'use strict';

// ---------- 单店模型（勇哥方法论泛化：保本线先行） ----------
async function loadStore() {
  try {
    const [r, p] = await Promise.all([api('/api/store/presets'), api('/api/profiles')]);
    state.store.presets = r.presets || [];
    state.store.profiles = p.items || [];
  } catch (_) {}
  render();
}

// ---------- 单店档案（存档复用：把店参数沉淀为可复用资产） ----------
async function saveStoreProfile() {
  const f = state.store.form;
  const name = (state.store.profileName || '').trim() || '我的店';
  state.store.savingProfile = true;
  render();
  try {
    await api('/api/store/profile', 'POST', {
      name,
      biz_type: state.store.bizType,
      gross_margin: f.gross_margin ? (parseFloat(f.gross_margin) / 100) : null,
      rent: parseFloat(f.rent) || 0,
      salary: parseFloat(f.salary) || 0,
      utilities: parseFloat(f.utilities) || 0,
      total_investment: parseFloat(f.total_investment) || 0,
      cash_on_hand: parseFloat(f.cash_on_hand) || 0,
      traffic: f.traffic,
      competitor: f.competitor,
    });
    toast('档案已保存，随时可套用重算');
    const p = await api('/api/profiles');
    state.store.profiles = p.items || [];
  } catch (e) {
    toast(e.message);
  }
  state.store.savingProfile = false;
  render();
}

async function applyStoreProfile(id) {
  state.store.applyingProfile = id;
  render();
  try {
    const p = await api('/api/profile/' + id);
    if (p.error) { toast(p.error); return; }
    const f = state.store.form;
    f.gross_margin = p.gross_margin != null ? (p.gross_margin * 100).toFixed(0) : '';
    f.rent = p.rent || '';
    f.salary = p.salary || '';
    f.utilities = p.utilities || '';
    f.total_investment = p.total_investment || '';
    f.cash_on_hand = p.cash_on_hand || '';
    f.traffic = p.traffic || '一般';
    f.competitor = p.competitor || '一般';
    state.store.bizType = p.biz_type || '餐饮';
    state.store.profileName = p.name || '';
    state.store.result = null;
    state.store.diagnosis = null;
    toast('已套用「' + (p.name || '档案') + '」，日销从账本带入或手填');
  } catch (e) {
    toast(e.message);
  }
  state.store.applyingProfile = null;
  render();
}

async function delStoreProfile(id) {
  try {
    await api('/api/profile/' + id, 'DELETE');
    state.store.profiles = state.store.profiles.filter(p => p.id !== id);
    toast('档案已删除');
  } catch (e) {
    toast(e.message);
  }
  render();
}

function setStoreBiz(t) {
  state.store.bizType = t;
  state.store.form.gross_margin = '';  // 切换类型后让用户用业态默认毛利率
  state.store.result = null;
  state.store.diagnosis = null;
  render();
}

async function calcStoreModel() {
  const f = state.store.form;
  const nums = [f.daily_revenue, f.rent, f.salary, f.utilities, f.total_investment, f.cash_on_hand];
  if (nums.every(v => !String(v).trim())) { toast('至少填一项数据，让掌柜帮你算'); return; }
  state.store.loading = true;
  state.store.result = null;
  state.store.diagnosis = null;
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
    const r = await api('/api/store/diagnosis', 'POST', body);
    state.store.result = r.model;
    state.store.diagnosis = r.diagnosis;
    state.store.diagnosisAiUsed = r.ai_used;
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
    state.store.diagnosis = null;
    toast('已从账本带入');
  } catch (e) {
    toast(e.message);
  }
  state.store.ledgering = false;
  render();
}

// 渲染函数已拆到 store_render.js（避免单文件 >250 行）