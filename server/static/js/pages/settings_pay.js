// 收款账户渲染（从 settings.js 拆出，依赖 core.js 的 state/api/toast/render/esc）
'use strict';

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
