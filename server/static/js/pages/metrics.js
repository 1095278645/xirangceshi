// 运行指标页：AI 成本与性能看板
// 依赖 core.js 的 state/api/toast/render/esc
// 数据来自 ai.chat 每次调用的旁路记录（见 server/db_metrics.py）。
'use strict';

let _mxDays = 7;
let _mxData = null;
let _mxLoading = false;

async function loadMetrics() {
  _mxLoading = true;
  render();
  try {
    _mxData = await api('/api/metrics/ai?days=' + _mxDays);
  } catch (e) {
    toast(e.message);
    _mxData = null;
  }
  _mxLoading = false;
  render();
}

function setMetricsDays(d) {
  _mxDays = Number(d) || 7;
  loadMetrics();
}

function _mxMoney(v) {
  const n = Number(v || 0);
  return '¥' + (n < 1 ? n.toFixed(4) : n.toFixed(2));
}

function _mxRows(items, amountKey) {
  if (!items || !items.length) return '<div class="howto-text">暂无</div>';
  return items.map(x => `
    <div class="status-detail" style="display:flex;justify-content:space-between;gap:8px">
      <span>${esc(x.name)}</span>
      <span>${x.calls} 次 · ${_mxMoney(x.cost_yuan)} · ${x.avg_latency_ms}ms</span>
    </div>`).join('');
}

function renderMetrics() {
  const head = '<div class="hero"><div class="hero-title">运行指标</div>'
    + '<div class="hero-sub">AI 成本与性能看板</div></div>';
  const picker = `
  <div class="card">
    <div class="card-title">统计窗口</div>
    <div class="form-item">
      <select class="form-select" onchange="setMetricsDays(this.value)">
        ${[7, 30, 90].map(v => `<option value="${v}" ${v === _mxDays ? 'selected' : ''}>近 ${v} 天</option>`).join('')}
      </select>
    </div>
    <button class="btn-ghost" onclick="loadMetrics()">刷新</button>
    <div class="howto-text">数据来自每次 AI 调用的旁路记录；成本为按 token 用量 × 示例单价的估算值。</div>
  </div>`;

  if (_mxLoading) {
    return head + picker + '<div class="card"><div class="howto-text">加载中…</div></div>';
  }
  const d = _mxData;
  if (!d) {
    return head + picker
      + '<div class="card"><div class="howto-text">暂无数据：配置 API Key 并记几笔账后，这里会显示调用量、延迟与成本。</div></div>';
  }

  const lat = d.latency_ms || {};
  const tk = d.tokens || {};
  const rate = (d.success_rate == null) ? '—' : Math.round(d.success_rate * 100) + '%';
  const perOrder = (d.per_order_cost_est_yuan == null) ? '—' : _mxMoney(d.per_order_cost_est_yuan);

  return head + picker + `
  <div class="card">
    <div class="card-title">总览（近 ${d.window_days} 天）</div>
    <div class="status-detail">调用次数：${d.calls}　成功：${d.ok}　失败：${d.failed}　成功率：${rate}</div>
    <div class="status-detail">Token：输入 ${tk.prompt || 0} / 输出 ${tk.completion || 0} / 合计 ${tk.total || 0}</div>
    <div class="status-detail">延迟：平均 ${lat.avg || 0}ms　P50 ${lat.p50 || 0}ms　P95 ${lat.p95 || 0}ms　最大 ${lat.max || 0}ms</div>
    <div class="status-detail">估算成本：${_mxMoney(d.cost_est_yuan)}　窗口内记账：${d.orders_in_window || 0} 笔　每单成本：${perOrder}</div>
  </div>
  <div class="card">
    <div class="card-title">按业务域</div>
    ${_mxRows(d.by_domain)}
  </div>
  <div class="card">
    <div class="card-title">按模型</div>
    ${_mxRows(d.by_model)}
  </div>
  <div class="card">
    <div class="card-title">说明</div>
    <div class="howto-text">${esc(d.note || '')}</div>
    ${(d.unknown_priced_models && d.unknown_priced_models.length)
      ? `<div class="howto-text">未定价模型（按默认单价估算）：${esc(d.unknown_priced_models.join('、'))}</div>` : ''}
  </div>`;
}
