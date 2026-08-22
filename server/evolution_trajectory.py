"""evolution_trajectory.py — 对话轨迹业务层（借鉴 SkillClaw 的 Client Capture 双循环）

任务时循环：_run_team 每次执行后调用 record_trajectory，无损保存完整对话轨迹，
供任务后循环（distill / promote / 复盘）取用。采集零侵入：不阻塞主链路，
采集失败仅记审计事件、不抛错影响产出。
"""
from datetime import datetime

import db_evolution as dbe
import db_evolution_trajectory as dbt

__all__ = ["record_trajectory", "get_trajectories"]


def record_trajectory(domain, task, gene_id=None, sys_suffix="", user_tail="",
                      mode="competitive", employees=None, verdict="", final="",
                      adopted=None):
    """封装一条团队执行轨迹。employees 形如 [{"role","system","user","output"}]。

    任何异常都不向调用方抛出（采集失败不影响产出），保证采集零侵入。
    """
    try:
        # 结构化员工输入输出（含完整 system+user 上下文，供事后复盘）
        inputs = {}
        turns = []
        for e in (employees or []):
            inputs[e.get("role", "?")] = {
                "system": e.get("system", ""),
                "user": e.get("user", ""),
            }
            turns.append({"role": e.get("role", "?"), "output": e.get("output", "")})

        # 追加掌柜裁决
        turns.append({"role": "judge", "output": verdict or ""})

        dbt.save_trajectory(
            domain=domain, task=task, system_inputs=inputs, turns=turns,
            mode=mode, gene_id=gene_id, verdict=verdict, final=final,
            adopted=adopted,
        )
    except Exception:  # noqa: BLE001 —— 轨迹采集失败不阻塞主链路（降级兜底）
        dbe.log_event("trajectory_capture_failed", gene_id=gene_id, domain=domain,
                      details="failed to record trajectory")


def get_trajectories(domain, limit=10):
    """读取某域最近轨迹（供复盘/诊断）"""
    return dbt.get_recent_trajectories(domain, limit=limit)