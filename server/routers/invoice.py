"""发票台账域：销项/进项发票管理（亲民口径，为报税申报打基础）"""
import logging

from fastapi import APIRouter

import db
from schemas import InvoiceIn

log = logging.getLogger("invoice")
router = APIRouter(prefix="/api", tags=["invoice"])


@router.get("/invoices")
def list_invoices(kind: str | None = None, status: str | None = None):
    """发票清单；可按销项(out)/进项(in)过滤"""
    return db.list_invoices(kind, status)


@router.post("/invoices")
def save_invoice(data: InvoiceIn):
    """新增/更新发票"""
    iid = db.add_invoice(data.kind, data.party, data.invoice_no, data.amount,
                         data.rate, data.tax_amount, data.issued_date,
                         data.note, data.iid)
    return {"invoice_id": iid, "saved": True}


@router.get("/invoices/summary")
def invoice_summary(month: str | None = None):
    """发票台账汇总（亲民：开了多少票 / 收了多少进项票）"""
    return db.invoice_summary(month)


@router.post("/invoices/{iid}/void")
def void_invoice(iid: int):
    """作废发票"""
    return {"voided": db.void_invoice(iid)}