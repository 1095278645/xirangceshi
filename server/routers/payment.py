"""收款账户（二维码流水同步）：微信商户号 / 聚合支付"""
from fastapi import APIRouter, HTTPException

import db
import payment
from schemas import PaymentSourceIn

router = APIRouter(prefix="/api", tags=["payment"])


@router.get("/payment/sources")
def payment_sources():
    """收款账户列表（微信商户号 / 聚合支付）。api_v3_key 已脱敏。"""
    return {"sources": db.list_payment_sources()}


@router.post("/payment/sources")
def payment_source_save(data: PaymentSourceIn):
    """新增/更新收款账户。mchid 填 DEMO 即为演示模式（免商户资料体验全流程）。
    api_v3_key 传空串（或脱敏占位 ***）时保留原值不清除。"""
    if data.source_type not in ("wechat", "aggregate"):
        raise HTTPException(400, "source_type 仅支持 wechat / aggregate")
    sid = db.save_payment_source(
        source_type=data.source_type, name=data.name, mchid=data.mchid,
        appid=data.appid, cert_path=data.cert_path,
        private_key_path=data.private_key_path, api_v3_key=data.api_v3_key,
        enabled=data.enabled, sid=data.sid)
    return {"ok": True, "id": sid}


@router.delete("/payment/sources/{sid}")
def payment_source_delete(sid: int):
    db.delete_payment_source(sid)
    return {"ok": True}


@router.post("/payment/sources/{sid}/sync")
def payment_source_sync(sid: int, bill_date: str | None = None):
    """手动同步某账户账单（默认昨天）。"""
    try:
        result = payment.run_sync(sid, bill_date)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return result


@router.get("/payment/logs")
def payment_logs(limit: int = 30):
    """账单同步日志"""
    return {"logs": db.list_sync_logs(limit)}


@router.post("/payment/demo-clear")
def payment_demo_clear():
    """一键清空演示模式产生的流水（wx_trade_id 以 DEMO- 开头）"""
    n = payment.demo_clear()
    return {"ok": True, "deleted": n}


@router.post("/payment/sync-all")
def payment_sync_all():
    """手动触发一次全部启用账户的昨日账单同步"""
    return {"results": payment.run_daily_sync()}