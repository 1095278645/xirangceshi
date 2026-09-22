"""运行指标：AI 成本与性能看板。

对应缺口：项目大量调用大模型，却没有任何用量/耗时记录 ——
无法回答"一次记账花多少钱""P95 延迟多少""哪个业务域最费钱"。
数据由 db_metrics 在 ai.chat 每次调用时旁路落库，本路由只负责汇总与查询。
"""
from fastapi import APIRouter, Query

import metrics
import ai_quality
from db_metrics import list_ai_calls

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/metrics/ai")
def ai_metrics(days: int = Query(default=7, ge=1, le=90)):
    """AI 调用汇总：调用量/成功率、token、延迟 P50/P95、估算成本与每单成本。"""
    return metrics.summarize(days)


@router.get("/metrics/ai/capability")
def ai_capability_metrics(days: int = Query(default=7, ge=1, le=90)):
    """AI 掌柜能力基线：感知/记忆/决策/行动/反馈闭环 + 质量门禁状态。"""
    return ai_quality.capability_report(days)


@router.get("/metrics/ai/calls")
def ai_metric_calls(limit: int = Query(default=50, ge=1, le=500)):
    """最近的原始调用记录（排查慢调用/失败调用用）。"""
    return {"calls": list_ai_calls(limit)}
