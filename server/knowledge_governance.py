"""knowledge_governance.py — 知识资产的「体检与治理」聚合层

## 定位（对应参考图 2020→2026 的「管得住」）

前几格（Vector RAG → GraphRAG → LightRAG → LLM Wiki）解决的是**怎么找到、怎么看懂**；
这一格解决的是**怎么管住**：知识写下来之后，谁还记得它从哪来、还成不成立、能不能退回去。

本模块不自己存数据，只做三件事：

  1. **汇总**（`governance_summary`）：资产层（knowledge_assets）+ 关系索引层
     （shop_relations）的只读聚合，供 `/api/metrics/ai` 看板与前端"知识台账"用。
  2. **核验**（`verify_runtime`）：把 volatile 资产拿回真值上对一遍（运行期核验），
     漂移的标记出来，不许继续当结论用 —— 对应图末"当前动态事实回到运行时核验"。
  3. **导出/导入**（`export_bundle` / `import_bundle`）：把知识打包成**可审计的 JSON**，
     走 `safe_io` 原子写；导入是幂等的（按 asset_key 合并，不产生重复）。

## 设计取舍

- **两层可降级**：`shop_relations` 尚未合入时，汇总只报资产层，不抛异常。
  理由：知识资产治理是主链路，关系索引是增强层，不能让增强层的缺失拖垮主链路。
- **不调 AI、不联网**：体检必须能在无 Key 环境跑（与 skill_cards / shop_snapshot 一致）。
- **导出物落 `server/data/`**：该目录已在 `.gitignore`（铁律1），
  且 `safe_io.is_protected` 会拦住误写源码目录的路径。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("knowledge_governance")

__all__ = ["governance_summary", "verify_runtime", "export_bundle", "import_bundle",
           "capability_block", "review_knowledge", "knowledge_maintenance", "EXPORT_PREFIX"]

# 知识包文件名前缀（与 backup.EXPORT_PREFIX 同风格：一眼看出这是什么产物）
EXPORT_PREFIX = "ai_shopkeeper_knowledge_"
# 知识包格式版本：外部拿到这个包时要能判断"我读得懂吗"
BUNDLE_FORMAT = "xirang.knowledge.bundle/v1"


def _export_dir() -> Path:
    """导出目录：server/data/knowledge（已在 .gitignore 覆盖）"""
    from config import DATA_DIR
    d = Path(DATA_DIR) / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def governance_summary(*, verify: bool = False) -> dict:
    """知识治理总览：资产层 + 关系索引层的只读聚合。

    verify=True 时先跑一次运行期核验（会把 volatile 资产的 verify_ok 写回库）。
    任何一层不可用时只降级那一层（返回里带 `error` 说明），不整体抛异常。
    """
    import config
    out: dict = {
        "assets": {"total": 0, "by_kind": {}, "by_state": {}, "by_volatility": {},
                   "stale": 0, "latest_active": []},
        "relations": {"available": False, "edges_active": 0, "edges_total": 0,
                      "by_relation": {}, "health": {}, "checkpoint": None},
        "verification": None,
        "policy": {
            "drift_stable": getattr(config, "KNOWLEDGE_VERIFY_DRIFT_STABLE", 0.0),
            "drift_slow": getattr(config, "KNOWLEDGE_VERIFY_DRIFT_SLOW", 0.05),
            "drift_volatile": getattr(config, "KNOWLEDGE_VERIFY_DRIFT_VOLATILE", 0.20),
            "edge_stale_days": getattr(config, "KNOWLEDGE_EDGE_STALE_DAYS", 30),
        },
        "note": ("资产 = 带来源与状态的知识；关系 = 来自原始单据的边。"
                 "volatile 资产在运行期回到真值核验，漂移的标记为待复核。"),
    }

    if verify:
        try:
            out["verification"] = verify_runtime()
        except Exception as e:  # noqa: BLE001 —— 核验失败不影响总览
            log.warning("运行期核验失败（总览仍返回）：%s", e)
            out["verification"] = {"error": str(e)[:200]}

    try:
        import knowledge_assets as ka
        stats = ka.asset_stats()
        assets = ka.list_assets(state="active", limit=500)
        exposed = ka.expose_active(assets)
        out["assets"] = {
            "total": stats.get("total", 0),
            "by_kind": stats.get("by_kind", {}),
            "by_state": stats.get("by_state", {}),
            "by_volatility": stats.get("by_volatility", {}),
            "stale": sum(1 for a in exposed if a.get("stale")),
            "latest_active": stats.get("latest_active", []),
        }
        out["assets"]["exposed"] = exposed[:20]
    except Exception as e:  # noqa: BLE001 —— 资产层不可用时如实说明
        log.warning("知识资产层不可用：%s", e)
        out["assets"]["error"] = str(e)[:200]

    try:
        import shop_relations as sr
        s = sr.relations_summary()
        st = s.get("stats") or {}
        out["relations"] = {
            "available": True,
            "edges_active": st.get("edges_active", 0),
            "edges_total": st.get("edges_total", 0),
            "by_relation": st.get("by_relation", {}),
            "health": s.get("health") or {},
            "checkpoint": s.get("checkpoint"),
            "relation_types": s.get("relation_types", []),
        }
    except Exception as e:  # noqa: BLE001 —— 关系层是增强层，缺失不算故障
        log.warning("关系索引层不可用（降级为仅资产层）：%s", e)
        out["relations"]["error"] = str(e)[:200]

    return out


def verify_runtime(asset_id: str = "", limit: int = 50) -> dict:
    """运行期核验入口：把 volatile 资产拿回真值上对一遍。

    单条核验时先确认"它确实是 volatile 资产"——stable 资产（如技能卡片规则）
    没有运行期真值可对，硬验只会产出 unknown 噪声。
    """
    import knowledge_assets as ka
    if asset_id:
        asset = ka.get_asset(asset_id)
        if not asset:
            return {"checked": 0, "ok": 0, "drift": 0, "unknown": 0, "items": [],
                    "error": "资产不存在", "not_found": True}
        if asset.get("volatility") != "volatile":
            return {"checked": 0, "ok": 0, "drift": 0, "unknown": 0, "items": [],
                    "error": f"该资产挥发度为 {asset.get('volatility')}，不需要运行期核验"}
    return ka.verify_asset(asset_id or None, limit=limit)


def _collect_bundle(include_retired: bool = False) -> dict:
    """组装知识包内容（纯函数，便于测试与审计）。"""
    import knowledge_assets as ka
    states = ["active", "draft"] + (["retired"] if include_retired else [])
    assets = []
    for st in states:
        assets.extend(ka.list_assets(state=st, limit=500))

    edges: list = []
    try:
        import db_relations
        edges = db_relations.list_edges(active=True, limit=500)
    except Exception as e:  # noqa: BLE001
        log.warning("知识包导不出关系层（仅导出资产层）：%s", e)

    return {
        "format": BUNDLE_FORMAT,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {"assets": len(assets), "edges": len(edges)},
        "assets": ka.expose_active(assets),
        "edges": edges,
        "manifest": {
            "asset_kinds": list(ka.ASSET_KINDS),
            "asset_states": list(ka.ASSET_STATES),
            "volatilities": list(ka.VOLATILITIES),
            "source_kinds": list(ka.SOURCE_KINDS),
            "note": "assets 是知识本身，edges 是它依据的跨域关系；两者都可回到原始单据。",
        },
    }


def export_bundle(include_retired: bool = False) -> dict:
    """把知识打包成可审计的 JSON 文件（原子写，落在 server/data/knowledge/）。"""
    from safe_io import atomic_write_json, is_protected
    bundle = _collect_bundle(include_retired)
    name = f"{EXPORT_PREFIX}{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path = _export_dir() / name
    if is_protected(path):  # 铁律：受保护路径一律拒绝（导出物必须在 data/ 下）
        return {"ok": False, "error": f"受保护路径禁止写入: {path}"}
    try:
        atomic_write_json(path, bundle)
    except OSError as e:
        # 写盘失败（磁盘满/权限）不该变成裸 500：返回带原因的失败（路由转 4xx/5xx 明确说明）
        log.warning("知识包写盘失败：%s", e)
        return {"ok": False, "error": f"知识包写盘失败：{e}"}
    return {"ok": True, "file": str(path), "name": name,
            "counts": bundle["counts"], "format": BUNDLE_FORMAT}


def knowledge_maintenance(edge_days: int | None = None,
                          bundle_days: int | None = None) -> dict:
    """知识层数据保留：清理过保留期的失效关系边与历史知识包导出文件。

    为什么需要：边与导出文件都只增不减（独立复核指出 `prune_edges` 此前没有任何生产
    调用点）。由 `main._backup_loop` 每 6 小时幂等调用一次；任一子步骤失败只记 error。
    """
    import config
    out: dict = {"edges_pruned": 0, "bundles_pruned": 0, "errors": []}
    try:
        import db_relations
        out["edges_pruned"] = db_relations.prune_edges(edge_days)
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"edges: {e}")
    days = int(bundle_days if bundle_days is not None
               else getattr(config, "KNOWLEDGE_BUNDLE_KEEP_DAYS", 30))
    # 知识包文件按 mtime 清理；只删本模块导出的前缀，别碰目录里任何别的东西
    try:
        cutoff = datetime.now().timestamp() - days * 86400
        for bundle_file in _export_dir().glob(f"{EXPORT_PREFIX}*.json"):
            if bundle_file.stat().st_mtime < cutoff:
                bundle_file.unlink()
                out["bundles_pruned"] += 1
    except OSError as e:
        out["errors"].append(f"bundles: {e}")
    return out


def import_bundle(payload: dict | str) -> dict:
    """导入知识包（幂等：按 asset_key 合并，重复导入不产生重复资产）。

    只接受本格式的包；非法/损坏的包返回 ok=False 并说明原因，不抛异常 ——
    导入是"外部数据进门"，必须当成不可信输入处理。
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError) as e:
            return {"ok": False, "error": f"包不是合法 JSON：{e}"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "包格式错误：顶层必须是对象"}
    if payload.get("format") != BUNDLE_FORMAT:
        return {"ok": False, "error": f"不认识的包格式：{payload.get('format')!r}"
                                      f"（期望 {BUNDLE_FORMAT}）"}

    import knowledge_assets as ka
    created = unchanged = failed = 0
    errors: list[str] = []
    for item in payload.get("assets") or []:
        if not isinstance(item, dict):
            failed += 1
            continue
        try:
            res = ka.register_asset(
                item.get("kind", ""), item.get("subject", ""),
                item.get("statement", ""), evidence=item.get("evidence") or [],
                source_kind=item.get("source_kind") or "manual",
                confidence=item.get("confidence"))
            created += 1 if res.get("created") else 0
            unchanged += 0 if res.get("created") else 1
        except Exception as e:  # noqa: BLE001 —— 单条坏数据不拖垮整包导入
            failed += 1
            if len(errors) < 5:
                errors.append(f"{item.get('subject') or '?'}: {e}")
    return {"ok": True, "created": created, "unchanged": unchanged,
            "failed": failed, "errors": errors,
            "restored": (payload.get("counts") or {})}


def capability_block() -> dict:
    """给 `/api/metrics/ai/capability` 用的知识治理子块（薄封装，只挑要点）。"""
    s = governance_summary()
    a = s.get("assets", {})
    r = s.get("relations", {})
    return {
        "assets_total": a.get("total", 0),
        "assets_active": (a.get("by_state") or {}).get("active", 0),
        "assets_stale": a.get("stale", 0),
        "by_kind": a.get("by_kind", {}),
        "edges_active": r.get("edges_active", 0),
        "relations_healthy": bool((r.get("health") or {}).get("ok")),
        "policy": s.get("policy", {}),
        "note": "资产带来源/版本/状态；volatile 资产在运行期回到真值核验。",
    }


def review_knowledge(limit: int = 5, verify: bool = True) -> dict:
    """复盘卡上"这次结论的依据"：把本次复盘登记的资产挑出来给前端展示。

    读的是**已落库的 active 资产**（不是内存里的中间值），这样刷新页面后
    依据依然可查 —— 与项目"结论可查证"的一贯做法一致（见 /api/heartbeat/snapshot）。

    排序有讲究：优先给"这次复盘的结论 / 命中的技能 / 采纳归因"，事实类资产垫底。
    否则列表会被陈年事实行占满，店主看不到"掌柜这次凭什么这么说"。
    verify=True 时顺带做一次运行期核验（幂等、纯本地），失败不影响展示。
    """
    out: dict = {"items": [], "total": 0, "stale": 0, "verified_at": "", "note": ""}
    try:
        import knowledge_assets as ka
        stats = ka.asset_stats()
        out["total"] = stats.get("total", 0)
        exposed = ka.expose_active(ka.list_assets(state="active", limit=500))
        order = {"decision": 0, "attention": 1, "strategy": 2, "experience": 3,
                 "fact": 4, "profile": 5}
        exposed.sort(key=lambda a: (order.get(a.get("kind"), 9), a.get("subject") or ""))
        # **先截断再核验**：原先对全部 active 资产逐条核验（200 条时 200 次真值查询 +
        # 200 条 UPDATE），而界面只展示 limit 条 —— 读接口的写放大与延迟都不可接受
        # （独立复核实测：200 条资产时本函数耗时 12.4s）。
        exposed = exposed[:max(1, limit)]
        if verify:
            try:
                for a in exposed:
                    if a.get("volatility") == "volatile":
                        ka.verify_asset(a["asset_id"])
            except Exception as e:  # noqa: BLE001 —— 核验失败不影响"有依据可看"
                log.warning("复盘依据的运行期核验失败：%s", e)
            by_id = {r["asset_id"]: r for r in ka.list_assets(state="active", limit=500)}
            exposed = [ka.expose_active([by_id[a["asset_id"]]])[0]
                       if a["asset_id"] in by_id else a for a in exposed]
        out["items"] = exposed
        out["stale"] = sum(1 for a in exposed if a.get("stale"))
        out["verified_at"] = max((a.get("verified_at") or "" for a in exposed), default="")
        out["note"] = ("这些是掌柜这次结论的依据；标黄的表示动态事实已变、需再核一遍。"
                       if out["stale"] else "这些是掌柜这次结论的依据，均可回到原始单据。")
    except Exception as e:  # noqa: BLE001 —— 知识层异常不影响复盘展示
        log.warning("复盘依据读取失败（降级为空）：%s", e)
        out["error"] = str(e)[:200]
    return out
