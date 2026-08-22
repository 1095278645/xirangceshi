"""财务健康域：现金流滚动预测 / 预算管理 / 应收应付账龄"""
import logging
from datetime import date

from fastapi import APIRouter

import db
import finance
from schemas import BudgetIn, DebtIn, CashflowIn, SettleDebtIn

log = logging.getLogger("finance")
router = APIRouter(prefix="/api", tags=["finance"])

@router.get("/budgets")
def budgets(month: str | None = None):
    return db.list_budgets(month)

@router.post("/budgets")
def save_budget(data: BudgetIn):
    bid = db.save_budget(data.month, data.scope, data.amount,
                         data.category, data.note, data.bid)
    return {"budget_id": bid, "saved": True}

@router.get("/budgets/actual")
def budget_vs_actual(month: str | None = None):
    m = month or f"{date.today().year:04d}-{date.today().month:02d}"
    return db.budget_vs_actual(m)

@router.delete("/budgets/{bid}")
def remove_budget(bid: int):
    return {"deleted": db.delete_budget(bid)}

@router.get("/debts")
def debts(kind: str | None = None, status: str | None = None):
    return db.list_debts(kind, status)

@router.post("/debts")
def save_debt(data: DebtIn):
    did = db.add_debt(data.party, data.kind, data.amount,
                      data.due_date, data.note, data.did)
    return {"debt_id": did, "saved": True}

@router.get("/debts/aging")
def debt_aging():
    return db.aging_summary()

@router.post("/debts/{did}/settle")
def settle_debt(did: int, data: SettleDebtIn):
    r = db.settle_debt(did, data.settle_amount)
    return r or {"error": "not found"}

@router.delete("/debts/{did}")
def remove_debt(did: int):
    return {"deleted": db.delete_debt(did)}

@router.post("/cashflow")
def cashflow(data: CashflowIn):
    """现金流预测：取账本月均线 + 应收应付到期 → 滚动预测"""
    month = f"{date.today().year:04d}-{date.today().month:02d}"
    hist = db.monthly_history()
    flows = db.debt_month_flows(data.months, month)
    result = finance.forecast_cashflow(
        cash_on_hand=data.cash_on_hand,
        base_income=hist["base_income"],
        base_expense=hist["base_expense"],
        debt_flows=flows,
        months=data.months,
        safety_buffer=data.safety_buffer,
    )
    result["ai_used"] = False
    return result
