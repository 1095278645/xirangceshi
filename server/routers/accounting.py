"""会计闭环：科目余额表 / 利润表 / 资产负债表 / 期末结转

补齐会计循环的最后一环：原先有复式凭证但没有期末结转与财务报表，
所以只能"记账 + 算税"，代账会计拿去接不上。
"""
import logging

from fastapi import APIRouter, HTTPException, Query

import accounting
from schemas import ClosePeriodIn, OpeningBalanceIn

log = logging.getLogger("routers.accounting")
router = APIRouter(prefix="/api", tags=["accounting"])


@router.get("/accounting/trial-balance")
def trial_balance(period: str | None = Query(default=None,
                                             description="YYYY-MM；不传按全部凭证")):
    """科目余额表：各科目期初 / 借方 / 贷方 / 期末余额，并自检借贷是否平衡。"""
    try:
        return accounting.trial_balance(period)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/accounting/income-statement")
def income_statement(period: str | None = Query(default=None)):
    """利润表：收入 − 成本费用 = 净利润。"""
    try:
        return accounting.income_statement(period)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/accounting/balance-sheet")
def balance_sheet(as_of: str | None = Query(default=None, description="YYYY-MM-DD")):
    """资产负债表：资产 = 负债 + 所有者权益（含未结转的当期损益）。"""
    try:
        return accounting.balance_sheet(as_of)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/accounting/closings")
def closings():
    """已结转期间列表。"""
    return {"closings": accounting.list_closings()}


@router.post("/accounting/close")
def close(data: ClosePeriodIn):
    """期末结转：把损益类科目余额转入「本年利润」。

    若该期间已结转过，会先红冲旧的结转凭证再重新生成（支持更正后重算）。
    """
    try:
        return accounting.close_period(data.period, note=data.note)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/accounting/reopen")
def reopen(data: ClosePeriodIn):
    """反结转：冲销结转凭证并解除期间锁定（发现账目问题后重做用）。"""
    try:
        return accounting.reopen_period(data.period, reason=data.note)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/accounting/opening-balances")
def opening_balances():
    """期初余额列表（把历史账套接进来时用）。"""
    return {"balances": accounting.list_opening_balances()}


@router.post("/accounting/opening-balances")
def set_opening_balance(data: OpeningBalanceIn):
    """设置某科目的期初余额。"""
    try:
        return accounting.set_opening_balance(data.account_code, data.amount, data.note)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
