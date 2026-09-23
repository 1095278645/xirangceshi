"""自适应进化层：**只保留一个只读摘要端点**（批次 B 收敛）

为什么收敛：进化能力的输入信号（真值反馈）在实际运行中只接了一半，10 个端点
里 9 个没有任何前端消费者。"看不见、也转不动"的写接口留着只会让人误会它在工作。
现在把它定性为**内部离线机制**：编排仍由 `heartbeat.evolution_daily_check()` 驱动
（默认关闭，见 `config.evolution_enabled()`），对外只暴露一个只读摘要，供看板/排查。
"""
from fastapi import APIRouter

import config
import team_domains
import team_evolution

router = APIRouter(prefix="/api", tags=["evolution"])


@router.get("/evolution/summary")
def evolution_summary(domain: str = ""):
    """只读：进化层状态摘要（默认汇总全部已注册域）。

    同时返回是否启用，避免"端点有数据 ≠ 进化在跑"的误解。
    """
    if domain:
        return {"enabled": config.evolution_enabled(), "domain": domain,
                "summary": team_evolution.get_evolution_summary(domain)}
    return {
        "enabled": config.evolution_enabled(),
        "domains": {d: team_evolution.get_evolution_summary(d)
                    for d in team_domains.list_team_domains()},
    }
