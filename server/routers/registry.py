"""业务路由能力注册表：让「增减能力」不改动应用组装（main.py）。

对标 team_domains.TEAM_DOMAINS 的「声明式注册表」思路，把「多 agent 域可插拔」
推广到非 AI 的业务路由层：
  - main.py 不再硬编码 import / include 具体 router，只统一遍历本注册表挂载；
  - 新增业务域：新建 routers/xxx.py（暴露 router）+ 在本注册表登记一行；
  - 停用业务域：把该域 enabled 改为 False（保留声明，便于临时下线，不删代码）；
  - 删除业务域：删掉注册表行即可（文件可留可删）。
  无论增/停/删，应用组装（main.py）与各域流程代码都零改动。

每个域一份 dict：
  - name:    域标识（用于自检 / 日志 / 前端菜单）
  - module:  模块名（routers/ 下，例如 "orders"）
  - desc:    一句话说明
  - enabled: 是否启用（False 时从挂载列表剔除，但保留声明）
"""
from __future__ import annotations

import importlib

from fastapi import APIRouter


def _load_router(module: str) -> APIRouter:
    """按模块名加载 routers/{module} 并取 .router（惰性 import，注册表构建时不真正执行）"""
    return importlib.import_module(f"routers.{module}").router


# ---------------- 业务域注册表（增删能力的唯一入口，与 team_domains 同风格） ----------------
BUSINESS_DOMAINS = [
    {"name": "arch",      "module": "arch",      "desc": "领域上下文 / 任务队列 / 单店档案 / 心跳复盘", "enabled": True},
    {"name": "basic",     "module": "basic",     "desc": "健康检查 / AI 设置 / 文案生成",                 "enabled": True},
    {"name": "orders",    "module": "orders",    "desc": "记账 / 流水 / 凭证",                           "enabled": True},
    {"name": "customers", "module": "customers", "desc": "熟客 / 记忆 / 提醒",                           "enabled": True},
    {"name": "tax",       "module": "tax",       "desc": "税法计算 / 科目表",                            "enabled": True},
    {"name": "store",     "module": "store",     "desc": "单店经营模型",                                 "enabled": True},
    {"name": "report",    "module": "report",    "desc": "月度报表导出",                                 "enabled": True},
    {"name": "payment",   "module": "payment",   "desc": "收款账户 / 账单同步",                          "enabled": True},
    {"name": "finance",   "module": "finance",   "desc": "预算 / 应收应付 / 现金流滚动预测",             "enabled": True},
    {"name": "stock",     "module": "stock",     "desc": "库存进销存 / 补货过期预警",                    "enabled": True},
    {"name": "invoice",   "module": "invoice",   "desc": "发票台账（销项/进项）",                        "enabled": True},
    {"name": "evolution", "module": "evolution", "desc": "自适应进化层",                                 "enabled": True},
]


def get_enabled_domains() -> list[dict]:
    """已启用业务域声明列表（enabled=True）"""
    return [d for d in BUSINESS_DOMAINS if d.get("enabled", True)]


def get_routers() -> list[APIRouter]:
    """返回所有已启用业务域的 APIRouter（main.py 统一挂载）。
    新增 / 停用 / 删除能力只改 BUSINESS_DOMAINS 声明，本函数与 main.py 均无需改动。"""
    return [_load_router(d["module"]) for d in get_enabled_domains()]


def list_domains() -> list[dict]:
    """列出全部业务域声明（含停用域，供自检 / 前端菜单 / 日志）"""
    return [dict(d) for d in BUSINESS_DOMAINS]