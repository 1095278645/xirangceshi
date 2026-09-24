/* 双前端共享契约（唯一真源） —— 请勿直接修改副本！
 *
 * 本文件是网页端(H5)与微信小程序共用的**纯逻辑**：存储键、请求头、HTTP 错误归类、
 * UI 枚举（须与后端 schemas 的 Literal 保持一致）。
 *
 * 落地方式（避免引入构建步骤）：
 *   - 真源：shared/frontend_contract.js
 *   - 同步：python scripts/sync_frontend_contract.py
 *   - 防漂移：python scripts/check_frontend_contract.py（CI 必跑）
 * 副本带 `AUTO-GENERATED` 标记，被检查脚本比对；两副本必须与真源逐字节一致。
 *
 * 运行环境兼容：小程序用 module.exports；浏览器挂到全局 FRONTEND_CONTRACT。
 */
(function (root, factory) {
  var api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;   // 小程序
  if (root) root.FRONTEND_CONTRACT = api;                                      // 浏览器
})(typeof globalThis !== 'undefined' ? globalThis
  : (typeof self !== 'undefined' ? self : this), function () {
  'use strict';

  // ---- 本地存储键（两端必须同名，否则填了令牌/切了店后行为不一致）----
  var STORAGE_KEYS = {
    token: 'shop_access_token',
    shop: 'shop_current_id',
    baseUrl: 'shop_base_url'
  };

  // ---- 后端地址归一（去空白与结尾斜杠，避免拼出 //）----
  function normalizeBaseUrl(url) {
    return String(url || '').trim().replace(/\/+$/, '');
  }

  // ---- 请求头：令牌 + 当前店铺（多店按 X-Shop-Id 切账本）----
  function buildAuthHeaders(token, shopId) {
    var h = {};
    if (token) h['X-Shop-Token'] = token;
    if (shopId !== null && shopId !== undefined && shopId !== '' && !isNaN(Number(shopId))) {
      h['X-Shop-Id'] = String(Number(shopId));
    }
    return h;
  }

  // ---- HTTP 结果归类（两端共用同一套文案，避免"同一错误两种说法"）----
  // 返回 { ok, kind, message }；kind ∈ ok|auth|forbidden|error
  function classifyHttp(status, body) {
    if (status >= 200 && status < 300) return { ok: true, kind: 'ok', message: '' };
    var detail = body && body.detail;
    if (status === 401) {
      return { ok: false, kind: 'auth', message: '需要访问令牌：请到「设置」页填写访问令牌' };
    }
    if (status === 403) {
      // 403 是业务性拒绝（如"你不是这家店的成员"），必须透出后端原话
      return { ok: false, kind: 'forbidden', message: detail || '没有权限执行该操作' };
    }
    return { ok: false, kind: 'error', message: detail || ('请求失败：' + status) };
  }

  // ---- UI 枚举（**必须与 server/schemas.py 的 Literal 一致**）----
  // 这两处若不一致，前端能选中后端拒绝的值 —— 属于契约漂移，检查脚本会提醒。
  var UI_ENUMS = {
    traffic: ['差', '一般', '好'],                    // StoreModelIn.traffic
    competitor: ['多', '一般', '少'],                 // StoreModelIn.competitor
    invoiceKind: ['out', 'in'],                       // InvoiceIn.kind
    stockMovement: ['in', 'out', 'adj'],              // StockMoveIn.movement
    insightScene: ['copy', 'monthly', 'tax', 'customer', 'store']  // UnifiedInsightIn.scene
  };

  return {
    STORAGE_KEYS: STORAGE_KEYS,
    UI_ENUMS: UI_ENUMS,
    normalizeBaseUrl: normalizeBaseUrl,
    buildAuthHeaders: buildAuthHeaders,
    classifyHttp: classifyHttp
  };
});
