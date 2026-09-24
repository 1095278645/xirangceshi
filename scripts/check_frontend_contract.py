# -*- coding: utf-8 -*-
"""防漂移检查：两处副本必须与真源逐字节一致，且 UI 枚举与后端 Literal 一致。

用法（仓库根）：python scripts/check_frontend_contract.py   （CI 必跑）
退出码 0=通过，1=存在漂移/不一致。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "shared" / "frontend_contract.js"
TARGETS = [
    ROOT / "server" / "static" / "shared" / "frontend_contract.js",
    ROOT / "miniprogram" / "shared" / "frontend_contract.js",
]
MARK = ("/* AUTO-GENERATED from shared/frontend_contract.js —— 请勿手改；"
        "改真源后跑 scripts/sync_frontend_contract.py */\n")

# 与后端 schemas.py 的 Literal 对照（值 → schemas 里的类名）
EXPECT = {
    "traffic": "StoreModelIn.traffic",
    "competitor": "StoreModelIn.competitor",
    "invoiceKind": "InvoiceIn.kind",
    "stockMovement": "StockMoveIn.movement",
    "insightScene": "UnifiedInsightIn.scene",
}

problems = []

if not SRC.exists():
    problems.append(f"缺少真源：{SRC.relative_to(ROOT)}")
else:
    body = SRC.read_text(encoding="utf-8")
    for t in TARGETS:
        rel = t.relative_to(ROOT)
        if not t.exists():
            problems.append(f"缺少副本：{rel}（跑 scripts/sync_frontend_contract.py）")
            continue
        if t.read_text(encoding="utf-8") != MARK + body:
            problems.append(f"副本与真源不一致：{rel}（跑 scripts/sync_frontend_contract.py）")

    # 与后端 Literal 的一致性（只查值集合，不查顺序）
    schemas = (ROOT / "server" / "schemas.py")
    stext = schemas.read_text(encoding="utf-8") if schemas.exists() else ""

    def literal_of(field: str) -> set:
        m = re.search(rf"{field}\s*:\s*Literal\[([^\]]+)\]", stext)
        if not m:
            return set()
        return {x.strip().strip("'\"") for x in m.group(1).split(",")}

    def enum_of(name: str) -> set:
        m = re.search(rf"{name}:\s*\[([^\]]+)\]", body)
        if not m:
            return set()
        return {x.strip().strip("'\"") for x in m.group(1).split(",")}

    for name, loc in EXPECT.items():
        field = loc.split(".")[-1]
        be = literal_of(field)
        fe = enum_of(name)
        if not be:
            problems.append(f"后端 schemas 未找到 Literal 字段：{loc}")
        elif fe != be:
            problems.append(f"枚举漂移 {name}：前端 {sorted(fe)} ≠ 后端 {sorted(be)}（{loc}）")

    # LABELS 的键集必须与对应 UI_ENUMS 一致（transType 对应数据库的 income/expense）
    def label_keys(name: str) -> set:
        m = re.search(rf"{name}:\s*\{{([^}}]*)\}}", body)
        if not m:
            return set()
        keys = set()
        for entry in m.group(1).split(","):
            entry = entry.strip()
            if not entry:
                continue
            keys.add(entry.split(":", 1)[0].strip().strip("'\""))
        return keys

    for lab, enum_key in (("invoiceKind", "invoiceKind"), ("stockMovement", "stockMovement")):
        lk, ek = label_keys(lab), enum_of(enum_key)
        if lk != ek:
            problems.append(f"标签键漂移 LABELS.{lab} {sorted(lk)} ≠ UI_ENUMS.{enum_key} {sorted(ek)}")
    if label_keys("transType") != {"income", "expense"}:
        problems.append(f"LABELS.transType 键应为 income/expense，实际 {sorted(label_keys('transType'))}")

print(f"前端契约真源：{SRC.relative_to(ROOT)}")
if problems:
    print("\n问题（必须修）：")
    for p in problems:
        print("  ✗ " + p)
    sys.exit(1)
print("  ✅ 两处副本与真源一致；UI 枚举与后端 Literal 一致")
sys.exit(0)
