"""
收款账户与账单同步统一入口（双通道：微信支付商户号 / 聚合支付）。

- 微信商户号（有执照/个体户）：wechat_pay 走微信支付 v3 交易账单接口
- 聚合支付（无执照）：aggregate_pay 预留收钱吧/付桥等服务商适配
- 演示模式：mchid 填 DEMO 即可体验完整「自动入账本」流程
"""
import logging
import sqlite3
from datetime import date, timedelta

import db
import aggregate_pay
import wechat_pay

log = logging.getLogger("payment")


def _import_txns(source, txns, bill_date):
    """把统一交易行写入 transactions（幂等：wx_trade_id 唯一索引去重）。
    微信流水不生成借贷凭证（区别于手记），source 标记来源便于溯源。"""
    imported = skipped = 0
    with db.get_conn() as conn:
        for t in txns:
            try:
                conn.execute(
                    "INSERT INTO transactions(customer_id, trans_type, category, item, "
                    "amount, counterparty, note, source, wx_trade_id, created_at) "
                    "VALUES(NULL,'income',?,?,?,?,?,?,?,?)",
                    (wechat_pay.CATEGORY, t["item"], t.get("amount", 0),
                     "", t.get("note", ""), source["source_type"],
                     t.get("wx_trade_id", ""), t.get("created_at")))
                imported += 1
            except sqlite3.IntegrityError:
                skipped += 1
    return imported, skipped


def run_sync(source_id, bill_date=None):
    """同步某账户某日（默认昨天）账单。返回结果 dict（含日志 id）。"""
    source = db.get_payment_source(source_id)
    if not source:
        raise ValueError("收款账户不存在")
    bill_date = bill_date or (date.today() - timedelta(days=1)).isoformat()

    try:
        if source["source_type"] == "wechat":
            txns = wechat_pay.fetch_trade_bill(source, bill_date)
        else:
            txns = aggregate_pay.fetch_aggregate_bill(source, bill_date)
    except Exception as e:  # noqa: BLE001
        log.error("sync fail source=%s date=%s: %s", source_id, bill_date, e)
        log_id = db.add_sync_log(source_id, bill_date, status="error", error=str(e)[:300])
        return {"ok": False, "error": str(e), "log_id": log_id}

    imported, skipped = _import_txns(source, txns, bill_date)
    status = "empty" if not txns else "success"
    log_id = db.add_sync_log(source_id, bill_date, status=status,
                             fetched=len(txns), imported=imported, skipped=skipped)
    return {"ok": True, "bill_date": bill_date, "fetched": len(txns),
            "imported": imported, "skipped": skipped, "log_id": log_id}


def run_daily_sync():
    """每日自动同步：所有启用账户拉取昨天账单（已同步过的日期自动跳过）。"""
    results = []
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    for source in db.list_payment_sources():
        if not source["enabled"]:
            continue
        if db.last_sync_date(source["id"]) == yesterday:
            continue
        try:
            results.append({"source_id": source["id"], **run_sync(source["id"])})
        except Exception as e:  # noqa: BLE001
            results.append({"source_id": source["id"], "ok": False, "error": str(e)})
    return results


def demo_clear():
    """清空演示流水（wx_trade_id 以 DEMO- 开头）。返回删除条数。"""
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM transactions WHERE wx_trade_id LIKE 'DEMO-%'")
        return cur.rowcount