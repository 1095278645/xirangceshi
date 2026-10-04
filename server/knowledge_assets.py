"""knowledge_assets.py — 知识资产治理层：来源 / 版本 / 状态 / 登记与查询

把「一段 LLM 文本」变成「一件可追责的资产」（对应参考图「管得住」/ LLM Wiki · OKF+Git）：
有来源（source_kind/source_ref/evidence，不接受无来源的断言）、有版本（内容变了旧行转
`superseded` 并指向新行，可回滚可追踪）、有状态（draft/active/superseded/retired）、
可核验（`volatile` 资产在运行期回到真值重算，见 knowledge_verify）。

设计取舍：
  - 不 import db.py 顶层（避免环），函数内 `from db import get_conn`；
  - 不调 AI、不联网，纯本地算法 —— 无 Key 环境也要能跑（与 skill_cards / shop_snapshot 一致）；
  - 阈值全部读 config.KNOWLEDGE_VERIFY_*（铁律：不散落魔法数），并做 getattr 容错读取；
  - 铁律5：kind / state / volatility 是有限取值，非法输入一律 ValueError，不静默兜底；
  - 运行期核验按职责搬到 `knowledge_verify.py`（原文件超 400 行硬限），文件末尾原样
    re-export，`knowledge_assets.xxx` 的调用方式完全不变（与 db.py 聚合 db_* 的做法一致）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime

import config

log = logging.getLogger("knowledge_assets")

__all__ = [
    "ASSET_STATES", "ASSET_KINDS", "VOLATILITIES", "SOURCE_KINDS", "VOLATILITY_BY_SOURCE",
    "DIRTY_STATES", "RUNTIME_SOURCES", "to_evidence", "source_tolerance", "content_hash",
    "register_asset", "get_asset", "list_assets", "asset_stats", "expose_active", "backfill",
    "build_verifiers", "check_volatility", "verify_asset",   # 实现见 knowledge_verify.py
]

# ---------------- 有限取值枚举（唯一真源；schemas.py 的 Literal 与之对齐） ----------------

ASSET_STATES = ("draft", "active", "superseded", "retired")
ASSET_KINDS = ("decision", "experience", "strategy", "attention", "fact", "profile")
VOLATILITIES = ("stable", "slow", "volatile")
SOURCE_KINDS = ("snapshot", "transaction", "invoice", "product", "customer", "ledger",
                "evolution", "skill_card", "computed", "manual")
VOLATILITY_BY_SOURCE = {   # 来源 → 默认挥发度（稳定资产 vs 当前动态事实）
    "snapshot": "volatile", "transaction": "volatile", "ledger": "volatile",
    "invoice": "slow", "product": "slow", "customer": "slow",
    "evolution": "stable", "skill_card": "stable", "computed": "slow", "manual": "stable",
}
DIRTY_STATES = ("draft", "superseded", "retired")   # 不可直接当结论用的状态
RUNTIME_SOURCES = ("cash_runway_months", "customer_count", "customer_visits",
                   "month_income", "month_expense", "month_balance", "month_purchase",
                   "invoice_in_month", "low_stock_count", "expiring_count",
                   "stale_customer_days", "today_balance", "today_income", "today_expense",
                   "gross_margin", "breakeven_daily")

DEFAULT_CONFIDENCE_WITH_EVIDENCE = 0.6
DEFAULT_CONFIDENCE_NO_EVIDENCE = 0.4
EVIDENCE_MAX_ITEMS = 5
EVIDENCE_MAX_CHARS = 200
STATEMENT_MAX_CHARS = 2000      # 正文入库上限（与 schemas.KnowledgeAssetIn.max_length 对齐）
# 挥发度 → (config 属性名, 兜底默认值)：stable 必须逐字一致，slow/volatile 允许相对漂移
_TOLERANCE_ATTR = {
    "stable": ("KNOWLEDGE_VERIFY_DRIFT_STABLE", 0.0),
    "slow": ("KNOWLEDGE_VERIFY_DRIFT_SLOW", 0.05),
    "volatile": ("KNOWLEDGE_VERIFY_DRIFT_VOLATILE", 0.20),
}
_EVIDENCE_KEYS = ("value", "statement", "name", "text", "label")
# asset_id 当日序号缓存（写法对齐 db_evolution_audit._seq，但**不 import** 它）
_SEQ_CACHE = {"date": "", "seq": 0}


# ---------------- 基础工具 ----------------

def to_evidence(items) -> list[str]:
    """evidence 归一化：非空字符串列表（最多 5 条、单条 200 字），兼容 str/dict/list。"""
    if items is None:
        return []
    if isinstance(items, str):
        items = [items]
    elif isinstance(items, dict):
        items = [items]
    elif not isinstance(items, (list, tuple, set)):
        items = [items]

    out: list[str] = []
    for item in items:
        text = ""
        if isinstance(item, dict):
            for key in _EVIDENCE_KEYS:
                value = item.get(key)
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    break
            if not text:
                try:
                    text = json.dumps(item, ensure_ascii=False)
                except (TypeError, ValueError):
                    text = str(item)
        else:
            text = str(item).strip()
        if not text:
            continue
        out.append(text[:EVIDENCE_MAX_CHARS])
        if len(out) >= EVIDENCE_MAX_ITEMS:
            break
    return out


def source_tolerance(volatility: str) -> float:
    """挥发度 → 容许漂移比例：stable 0.0 / slow 0.05 / volatile 0.20；未知按 volatile。"""
    attr, default = _TOLERANCE_ATTR.get(volatility, _TOLERANCE_ATTR["volatile"])
    try:
        return float(getattr(config, attr, default))
    except (TypeError, ValueError):   # 配置被人改坏时不崩，退回契约默认值
        return float(default)


def content_hash(statement: str, source_kind: str, evidence: list[str]) -> str:
    """知识内容指纹：来源 + 正文 + 证据，用于判断"同样的知识是否已经登记过"。"""
    payload = "\n".join([source_kind or "", statement or "", *(evidence or [])])
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve_volatility(volatility, source_kind):
    """显式挥发度优先；None 时按来源推断。推断结果也必须是合法枚举。"""
    if volatility is None:
        volatility = VOLATILITY_BY_SOURCE.get(source_kind or "", "volatile")
    if volatility not in VOLATILITIES:
        raise ValueError(f"非法 volatility：{volatility!r}（合法值：{VOLATILITIES}）")
    return volatility


def _resolve_confidence(confidence, state, evidence):
    if confidence is None:
        if state == "active" and evidence:
            return DEFAULT_CONFIDENCE_WITH_EVIDENCE
        return DEFAULT_CONFIDENCE_NO_EVIDENCE
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        raise ValueError(f"非法 confidence：{confidence!r}（应为 0~1 的数值）") from None
    return max(0.0, min(1.0, value))


def _next_asset_id(conn) -> str:
    """AS-<YYYYMMDD>-<当日序号3位>；进程内缓存 + 存在性校验，避免重启后冲突。"""
    today = datetime.now().strftime("%Y%m%d")
    if _SEQ_CACHE["date"] != today:
        _SEQ_CACHE["date"] = today
        _SEQ_CACHE["seq"] = 0
    while True:
        _SEQ_CACHE["seq"] += 1
        candidate = f"AS-{today}-{_SEQ_CACHE['seq']:03d}"
        hit = conn.execute("SELECT 1 FROM knowledge_assets WHERE asset_id=?",
                           (candidate,)).fetchone()
        if not hit:
            return candidate


def _row_dict(row) -> dict:
    """sqlite3.Row → dict：evidence_json 解析成 evidence 列表，verify_ok 转 bool。"""
    d = dict(row)
    raw = d.get("evidence_json")
    evidence: list[str] = []
    if isinstance(raw, str) and raw.strip():
        try:
            evidence = to_evidence(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            evidence = to_evidence(raw)
    elif isinstance(raw, (list, tuple)):
        evidence = to_evidence(raw)
    d["evidence"] = evidence
    d["verify_ok"] = bool(d.get("verify_ok", 1))
    try:
        d["confidence"] = float(d.get("confidence") or 0.0)
    except (TypeError, ValueError):
        d["confidence"] = 0.0
    try:
        d["version"] = int(d.get("version") or 1)
    except (TypeError, ValueError):
        d["version"] = 1
    return d


# ---------------- 登记（幂等 + 版本化） ----------------

def register_asset(kind, subject, statement, *, evidence=None, source_kind="",
                   source_ref="", volatility=None, state="active",
                   confidence=None, asset_key=None) -> dict:
    """登记一件知识资产。返回 {asset, created, changed, superseded}。

    幂等：同一 asset_key 且内容指纹相同 → 不新建行，只刷新 updated_at。
    版本化：内容变了 → 旧行 state=superseded / valid_to=now / superseded_by=新 id，
    新行 version=旧+1 / valid_from=now。这样"改知识"永远可回滚、可追踪。
    """
    if kind not in ASSET_KINDS:
        raise ValueError(f"非法 kind：{kind!r}（合法值：{ASSET_KINDS}）")
    if state not in ASSET_STATES:
        raise ValueError(f"非法 state：{state!r}（合法值：{ASSET_STATES}）")
    volatility = _resolve_volatility(volatility, source_kind)

    subject = str(subject or "")
    statement = str(statement or "")
    # 长度上限（与 schemas.KnowledgeAssetIn 的 max_length 对齐；AI 抽取路径也走这里，
    # 防止"内部调用绕过请求校验"把 10 万字正文塞进库并原样回吐给列表接口）。
    subject = subject[:EVIDENCE_MAX_CHARS]            # 200，与单条证据同量级
    statement = statement[:STATEMENT_MAX_CHARS]       # 2000，AI 抽取路径已先截到 1200
    ev = to_evidence(evidence)
    key = asset_key or f"{kind}:{subject}"
    digest = content_hash(statement, source_kind, ev)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conf = _resolve_confidence(confidence, state, ev)
    # 新建的 volatile 资产**从未核验过**：verify_ok 必须从"未通过"起步，
    # 否则台账会把"刚登记、没对过真值"当成"已核验无误"（实测发现的过度乐观默认值）。
    # 稳定资产（技能卡片/进化基因）没有运行期真值，标记为"不需要核验"= 1。
    fresh_verify_ok = 0 if volatility == "volatile" else 1

    from db import get_conn
    with get_conn() as conn:
        # BEGIN IMMEDIATE：登记是"读-改-写"，必须先把写锁拿到手，否则两个线程可同时
        # 走"没找到 active"分支造出两条 version=1 的 active 行（另由部分唯一索引兜底）。
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            pass                      # 已在事务中（如调用方包了一层）就沿用外层事务
        row = conn.execute(
            "SELECT * FROM knowledge_assets WHERE asset_key=? "
            "AND state IN ('active','draft') "
            "ORDER BY version DESC LIMIT 1", (key,)).fetchone()
        new_id = ""
        if row is not None:
            old = _row_dict(row)
            same = content_hash(old.get("statement", ""), old.get("source_kind", ""),
                                old.get("evidence") or []) == digest
            if same:
                # 内容没变：草稿不顶掉在用结论；状态不同则显式迁移（如 draft→active 升级），
                # 不新增版本、不新增行。
                if old.get("state") != state and state != "draft":
                    conn.execute(
                        "UPDATE knowledge_assets SET state=?, updated_at=? WHERE asset_id=?",
                        (state, now, old["asset_id"]))
                    old["state"] = state
                else:
                    conn.execute("UPDATE knowledge_assets SET updated_at=? WHERE asset_id=?",
                                 (now, old["asset_id"]))
                old["updated_at"] = now
                return {"asset": old, "created": False, "changed": False, "superseded": None}
            if old.get("state") == "draft" and state == "draft":
                # 草稿改草稿：原地替换，不牵动在用结论
                conn.execute("DELETE FROM knowledge_assets WHERE asset_id=?", (old["asset_id"],))
                version, superseded = int(old.get("version") or 1), None
            elif old.get("state") == "active" and state == "draft":
                # 已经有一条在用结论：草稿不许把它顶掉（否则该 key 会一条 active 都不剩），
                # 也不新建并行草稿（与"每个 key 至多一条在用/草稿"的约束冲突）。
                # 直接返回在用结论 + 说明 —— 调用方据此知道"这条草稿没落库"。
                old["note"] = "已存在在用结论，草稿未落库（改动请直接以 active 登记）"
                return {"asset": old, "created": False, "changed": False, "superseded": None}
            else:
                new_id = _next_asset_id(conn)
                conn.execute(
                    "UPDATE knowledge_assets SET state='superseded', valid_to=?, "
                    "superseded_by=?, updated_at=? WHERE asset_id=?",
                    (now, new_id, now, old["asset_id"]))
                version = int(old.get("version") or 1) + 1
                superseded = old["asset_id"]
            if not new_id:
                new_id = _next_asset_id(conn)
        else:
            new_id = _next_asset_id(conn)
            version = 1
            superseded = None

        # asset_id 冲突（同日序号在多进程下可能撞）→ 换一个序号重试，不把 IntegrityError 抛成 500
        for attempt in range(5):
            try:
                conn.execute(
                    "INSERT INTO knowledge_assets(asset_id, asset_key, version, kind, state, "
                    "volatility, subject, statement, evidence_json, source_kind, source_ref, "
                    "confidence, drift_note, created_at, updated_at, verified_at, verify_ok, "
                    "valid_from, valid_to, superseded_by) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (new_id, key, version, kind, state, volatility, subject, statement,
                     json.dumps(ev, ensure_ascii=False), source_kind, source_ref, conf, "",
                     now, now, "", fresh_verify_ok, now, "", ""))
                break
            except sqlite3.IntegrityError:
                if attempt == 4:
                    raise
                new_id = _next_asset_id(conn)
        fresh = conn.execute("SELECT * FROM knowledge_assets WHERE asset_id=?",
                             (new_id,)).fetchone()
    return {"asset": _row_dict(fresh), "created": True, "changed": True,
            "superseded": superseded}


# ---------------- 查询 ----------------

def get_asset(asset_id) -> dict | None:
    """按 asset_id 读一件资产（evidence 已解析、verify_ok 已转 bool）。"""
    if not asset_id:
        return None
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM knowledge_assets WHERE asset_id=?",
                           (asset_id,)).fetchone()
    return _row_dict(row) if row else None


def list_assets(kind=None, state=None, volatility=None, subject=None, limit=50) -> list[dict]:
    """按 kind/state/volatility/subject 过滤，updated_at DESC, version DESC；limit 上限 500。"""
    where, params = [], []
    for column, value in (("kind", kind), ("state", state),
                          ("volatility", volatility), ("subject", subject)):
        if value:
            where.append(f"{column}=?")
            params.append(value)
    sql = "SELECT * FROM knowledge_assets"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC, version DESC LIMIT ?"
    try:
        cap = max(1, min(int(limit or 50), 500))
    except (TypeError, ValueError):
        cap = 50
    params.append(cap)
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_dict(r) for r in rows]


def asset_stats() -> dict:
    """资产总览：总数 + 三个维度分布 + 最近 active 5 条 + 运行期可核验来源清单。"""
    from db import get_conn
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM knowledge_assets").fetchone()["c"]

        def _group(column):
            return {r["k"]: r["c"] for r in conn.execute(
                f"SELECT {column} AS k, COUNT(*) AS c FROM knowledge_assets GROUP BY {column}")}

        by_kind, by_state, by_volatility = _group("kind"), _group("state"), _group("volatility")
        rows = conn.execute(
            "SELECT asset_id, kind, subject, statement, volatility, verified_at, verify_ok "
            "FROM knowledge_assets WHERE state='active' "
            "ORDER BY updated_at DESC, version DESC LIMIT 5").fetchall()
    latest = [{"asset_id": r["asset_id"], "kind": r["kind"], "subject": r["subject"],
               "statement": r["statement"], "volatility": r["volatility"],
               "verified_at": r["verified_at"], "verify_ok": bool(r["verify_ok"])}
              for r in rows]
    return {"total": int(total or 0), "by_kind": by_kind, "by_state": by_state,
            "by_volatility": by_volatility, "latest_active": latest,
            "runtime_sources": list(RUNTIME_SOURCES)}


def expose_active(assets: list[dict]) -> list[dict]:
    """转成给掌柜/前端用的最小结构；statement 过一遍 plain_language.polish()。"""
    from plain_language import polish
    out = []
    for asset in assets or []:
        if not asset:
            continue
        volatility = asset.get("volatility") or "volatile"
        verify_ok = bool(asset.get("verify_ok", True))
        out.append({
            "asset_id": asset.get("asset_id", ""),
            "kind": asset.get("kind", ""),
            "subject": asset.get("subject", ""),
            "statement": polish(asset.get("statement") or ""),
            "volatility": volatility,
            "confidence": asset.get("confidence"),
            "evidence": asset.get("evidence") or to_evidence(asset.get("evidence_json")),
            "source_kind": asset.get("source_kind", ""),
            "version": asset.get("version", 1),
            "verified_at": asset.get("verified_at", ""),
            "verify_ok": verify_ok,
            "stale": volatility == "volatile" and not verify_ok,
        })
    return out


# 运行期核验实现见 knowledge_verify.py（搬家，API 不变）；放末尾 re-export 避免循环导入。
from knowledge_verify import build_verifiers, check_volatility, verify_asset  # noqa: E402,F401
# 回填实现见 knowledge_backfill.py（同样是搬家，API 不变）。
from knowledge_backfill import backfill  # noqa: E402,F401
