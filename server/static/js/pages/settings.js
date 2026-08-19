// 设置页：AI 模型配置 + 收款账户（依赖 core.js 的 state/api/toast/render）
'use strict';

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
      // 后端已脱敏：回传 *** 或空串均表示「保留原 Key」，不覆盖
      api_v3_key: f.api_v3_key.trim() === '***' ? '' : f.api_v3_key.trim(), enabled: f.enabled,
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

// ---------- 渲染 ----------
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
    <div class="status-detail">服务地址：${esc(state.baseUrl)}</div>
    <div class="status-detail">模型：${esc(state.model)}</div>
    ${state.aiEnabled ? '' : '<div class="status-detail">兜底模式记账/熟客仍可用，智能解析与提醒受限。</div>'}
  </div>
  <div class="card">
    <div class="card-title">AI 模型配置</div>
    <div class="form-item">
      <label class="form-label">选择大模型</label>
      <select class="form-select" onchange="selectProvider(this.value)">
        ${provs.map(p => `<option value="${esc(p.id)}" ${p.id === state.provider ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}
      </select>
    </div>
    <div class="form-item">
      <label class="form-label">${esc(keyLabel)}</label>
      <input class="form-input" type="password" placeholder="粘贴你的 API Key"
        value="${esc(state.apiKeyInput)}" oninput="state.apiKeyInput=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">Base URL${state.provider !== 'custom' ? '（已自动填充，可改）' : ''}</label>
      <input class="form-input" value="${esc(state.baseUrlInput)}" oninput="state.baseUrlInput=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">模型${state.provider !== 'custom' ? '（已自动填充，可改）' : ''}</label>
      <input class="form-input" value="${esc(state.modelInput)}" oninput="state.modelInput=this.value" />
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
        <strong>${esc(s.name || '(未命名)')}</strong>
        <span class="pay-mchid">商户号：${esc(s.mchid || '-')}</span>
        <span class="pay-status">${s.enabled ? '✅ 已启用' : '⏸ 未启用'}</span>
      </div>
      <div class="pay-row-actions">
        <button class="btn-mini" onclick="syncPaySource(${s.id})" ${state.paySyncing ? 'disabled' : ''}>同步</button>
        <button class="btn-mini btn-danger" onclick="deletePaySource(${s.id}, this.dataset.name)" data-name="${esc(s.name || '')}">删除</button>
      </div>
    </div>`).join('') : '<div class="howto-text">还没有收款账户。填商户号 DEMO 即可体验演示模式，无需任何商户资料。</div>';
  const logsHtml = state.payLogs.length ? state.payLogs.slice(0, 5).map(l => `
    <div class="log-row">
      <span class="log-status ${l.status}">${l.status === 'success' ? '✓' : l.status === 'empty' ? '○' : '✗'}</span>
      <span class="log-text">${esc(l.source_name || '账户')}</span>
      <span class="log-text">${esc(l.bill_date)}</span>
      <span class="log-text">${l.status === 'error' ? esc((l.error || '失败').slice(0, 30)) : `新增 ${l.imported} 笔`}</span>
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
      <input class="form-input" placeholder="如：店里收款码" value="${esc(f.name)}" oninput="state.payForm.name=this.value" />
    </div>
    ${f.source_type === 'wechat' ? `
    <div class="form-item">
      <label class="form-label">微信支付商户号（mchid）</label>
      <input class="form-input" placeholder="无商户资料可填 DEMO 体验演示模式" value="${esc(f.mchid)}" oninput="state.payForm.mchid=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">AppID（可选）</label>
      <input class="form-input" placeholder="小程序/公众号 AppID" value="${esc(f.appid)}" oninput="state.payForm.appid=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">商户 API 证书路径（apiclient_cert.pem）</label>
      <input class="form-input" placeholder="C:\\cert\\apiclient_cert.pem" value="${esc(f.cert_path)}" oninput="state.payForm.cert_path=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">商户 API 私钥路径（apiclient_key.pem）</label>
      <input class="form-input" placeholder="C:\\cert\\apiclient_key.pem" value="${esc(f.private_key_path)}" oninput="state.payForm.private_key_path=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">APIv3 密钥</label>
      <input class="form-input" type="password" placeholder="在微信支付商户平台设置" value="${esc(f.api_v3_key)}" oninput="state.payForm.api_v3_key=this.value" />
    </div>
    <div class="howto-text">需要：有营业执照 → 微信支付商户平台开通「交易账单」权限 → 下载 API 证书。</div>
    ` : `
    <div class="form-item">
      <label class="form-label">聚合支付商户号</label>
      <input class="form-input" placeholder="收钱吧/付呗等服务商商户号" value="${esc(f.mchid)}" oninput="state.payForm.mchid=this.value" />
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