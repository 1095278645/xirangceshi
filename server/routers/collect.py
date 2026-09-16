"""收款即入账：收款请求 / 公开收款页 / 确认入账

两条路径，鉴权要求不同：
  - `/api/collect/*`：店主侧（创建/确认/取消），受访问令牌保护
  - `/api/pay/{token}`：**顾客侧公开**，凭不可猜的 token 访问，
    必须在 auth 的公开路径白名单里，否则顾客扫码打不开
"""
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import HTMLResponse

import db
import notifications
from schemas import CollectionConfirmIn, CollectionIn

log = logging.getLogger("routers.collect")
router = APIRouter(prefix="/api", tags=["collect"])

# 顾客侧公开收款页（内联 HTML，避免额外静态资源；金额等敏感信息由 API 拉取，
# 页面本身不含数据，所以公开它没有泄露风险）
_PAY_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>扫码付款</title>
<style>
  body { margin:0; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background:#faf5f1; color:#333; display:flex; justify-content:center; }
  .wrap { width:100%; max-width:420px; padding:24px 18px 40px; }
  .hero { background:linear-gradient(135deg,#b4532a,#e07b39); color:#fff;
          border-radius:0 0 24px 24px; padding:28px 22px 34px; }
  .hero h1 { font-size:22px; margin:0 0 6px; }
  .hero p { margin:0; opacity:.9; font-size:14px; }
  .card { background:#fff; border-radius:16px; padding:22px; margin-top:-18px;
          box-shadow:0 2px 12px rgba(0,0,0,.06); }
  .amount { font-size:40px; font-weight:700; color:#b4532a; text-align:center; margin:8px 0 4px; }
  .item { text-align:center; color:#888; font-size:15px; margin-bottom:20px; }
  .field { margin-bottom:14px; }
  .field label { display:block; font-size:13px; color:#888; margin-bottom:6px; }
  .field input { width:100%; box-sizing:border-box; padding:12px; font-size:16px;
                 border:1px solid #e5ded7; border-radius:10px; background:#fff; }
  .btn { width:100%; padding:15px; font-size:17px; font-weight:600; color:#fff;
         background:#b4532a; border:none; border-radius:12px; margin-top:6px; }
  .btn:disabled { background:#ccc; }
  .hint { text-align:center; color:#999; font-size:13px; margin-top:14px; line-height:1.7; }
  .done { text-align:center; padding:14px 0; }
  .done .big { font-size:44px; }
  .pay-qr { text-align:center; margin:14px 0; }
  .pay-qr img { width:200px; height:200px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1 id="shop">收款</h1>
    <p>请确认金额后付款</p>
  </div>
  <div class="card" id="card">加载中…</div>
</div>
<script>
const TOKEN = location.pathname.split('/').pop();
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function load() {
  const card = document.getElementById('card');
  let data;
  try {
    const res = await fetch('/api/pay/' + encodeURIComponent(TOKEN));
    if (!res.ok) throw new Error((await res.json()).detail || '链接已失效');
    data = await res.json();
  } catch (e) {
    card.innerHTML = '<div class="hint">' + esc(e.message) + '</div>';
    return;
  }
  document.getElementById('shop').textContent = data.shop_name || '收款';
  if (data.status === 'paid' || data.status === 'confirmed') {
    card.innerHTML = '<div class="done"><div class="big">✅</div>'
      + '<div class="amount">' + data.amount.toFixed(2) + ' 元</div>'
      + '<div class="hint">' + (data.status === 'confirmed'
          ? '店主已确认收款，谢谢惠顾' : '已提交，等店主确认') + '</div></div>';
    return;
  }
  if (data.status === 'cancelled') {
    card.innerHTML = '<div class="hint">该收款请求已取消，请联系店主</div>';
    return;
  }
  card.innerHTML = `
    <div class="amount">${data.amount.toFixed(2)} 元</div>
    <div class="item">${esc(data.item || '')}</div>
    <div class="field">
      <label>怎么称呼您？（可留空）</label>
      <input id="payer" placeholder="如：王阿姨" value="${esc(data.payer_name || '')}" />
    </div>
    <button class="btn" id="submit">我已付款</button>
    <div class="hint">资金请付到店主的收款码；点上面按钮只是告诉店主你付好了。</div>`;
  document.getElementById('submit').onclick = async () => {
    const btn = document.getElementById('submit');
    btn.disabled = true; btn.textContent = '提交中…';
    try {
      const res = await fetch('/api/pay/' + encodeURIComponent(TOKEN) + '/paid', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({payer_name: document.getElementById('payer').value})
      });
      if (!res.ok) throw new Error((await res.json()).detail || '提交失败');
      load();
    } catch (e) {
      btn.disabled = false; btn.textContent = '我已付款';
      alert(e.message);
    }
  };
}
load();
</script>
</body>
</html>
"""


# ---------------- 公开接口（顾客侧，必须在 auth 白名单里） ----------------

@router.get("/pay/{token}", response_class=HTMLResponse)
def pay_page(token: str):
    """公开收款页（顾客扫码打开）。页面不含数据，数据由下面的接口拉。"""
    return HTMLResponse(_PAY_PAGE)


@router.get("/pay/{token}/info")
def pay_info(token: str):
    """收款页需要的信息（金额/事由/状态）。不含任何其他经营数据。"""
    c = db.get_collection_by_token(token)
    if not c:
        raise HTTPException(404, "收款请求不存在或链接已失效")
    return {
        "shop_name": _shop_name(),
        "amount": c["amount"],
        "item": c["item"],
        "status": c["status"],
        "payer_name": c["payer_name"],
    }


@router.post("/pay/{token}/paid")
def pay_mark_paid(token: str, data: CollectionConfirmIn | None = None):
    """顾客点「我已付款」→ 标记待店主确认。幂等，可重复点。"""
    payer = (data.payer_name if data else "") or ""
    try:
        c = db.mark_collection_paid(token, payer_name=payer)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "status": c["status"], "amount": c["amount"]}


def _shop_name() -> str:
    """店名：优先取熟客档案里的经营主体名，没有则用默认。"""
    try:
        import config
        return config.load_settings().get("shop_name") or "巷子里的早餐铺"
    except Exception:  # noqa: BLE001
        return "巷子里的早餐铺"


# ---------------- 店主侧接口（受访问令牌保护） ----------------

@router.post("/collect/create")
def collect_create(data: CollectionIn):
    """创建收款请求（生成收款链接/二维码）。"""
    try:
        c = db.create_collection(data.amount, item=data.item,
                                 customer_id=data.customer_id,
                                 note=data.note, payer_name=data.payer_name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "collection": c, "pay_url": f"/pay/{c['token']}",
            "pay_path": f"/pay/{c['token']}"}


@router.get("/collect/list")
def collect_list(status: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
    """收款请求列表（店主看"待确认"用）。"""
    try:
        items = db.list_collections(status=status, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    pending = [c for c in items if c["status"] in ("pending", "paid")]
    return {"collections": items, "pending_count": len(pending)}


@router.get("/collect/{cid}")
def collect_detail(cid: int):
    c = db.get_collection(cid)
    if not c:
        raise HTTPException(404, "收款请求不存在")
    return {"collection": c, "pay_url": f"/pay/{c['token']}"}


@router.post("/collect/{cid}/confirm")
def collect_confirm(cid: int, background: BackgroundTasks,
                    data: CollectionConfirmIn | None = None):
    """确认到账 → 自动入账（写收入 + 生成借贷凭证）+ 返回播报文本。

    推送放在 BackgroundTasks 里：在数据库写事务内做网络请求会占着写锁，
    拖慢其它写入；先落库、响应后再推送才是对的顺序。
    """
    item = data.item if data else None
    category = (data.category if data else None) or "主营业务收入"
    try:
        result = db.confirm_collection(cid, item=item, category=category)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    amount = (result.get("collection") or {}).get("amount")
    payer = (result.get("collection") or {}).get("payer_name") or "顾客"
    background.add_task(
        notifications.dispatch_event, "payment_received",
        f"收款 {amount:.2f} 元",
        f"{payer} 已付款 {amount:.2f} 元，已自动入账。")
    return result


@router.post("/collect/{cid}/cancel")
def collect_cancel(cid: int, data: CollectionConfirmIn | None = None):
    reason = (data.item if data else "") or ""
    try:
        c = db.cancel_collection(cid, reason=reason)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "collection": c}
