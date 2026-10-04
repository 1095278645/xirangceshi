# -*- coding: utf-8 -*-
"""判断用户的文案是 AI 生成还是规则降级：
1) 打线上接口，看响应里的 team/verdict/cached 与原文；
2) 本地用同一输入跑降级路径（ai_available=False），逐字对比。
"""
import json
import os
import sys
from pathlib import Path

import httpx
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ROOT = Path(r"C:\Users\Administrator\Desktop\xirang")
sys.path.insert(0, str(ROOT / "server"))

B = os.environ["XIRANG_BASE"].rstrip("/")
C = httpx.Client(base_url=B, headers={"X-Shop-Token": os.environ["XIRANG_TOKEN"]},
                 timeout=300, verify=False)

# 与截图里一致的输入（截图正文提到"巷子口那家早餐铺"、"冻品那笔1450"）
form = {"shop_name": "巷子里的早餐铺", "scene": "今日营业",
        "extra": "茶叶蛋卤久了便宜出，蔬菜明天到期", "customer_name": ""}

print("=" * 78)
print("① 线上响应（唯一 extra 以绕过缓存）")
print("=" * 78)
uniq = dict(form)
uniq["extra"] = form["extra"] + " " + str(os.urandom(3).hex())
r = C.post("/api/insights", json={"scene": "copy", "payload": uniq})
j = r.json()
print(f"HTTP {r.status_code}")
print("  cached :", j.get("cached"))
print("  gene_id:", j.get("gene_id"))
print("  combo  :", json.dumps(j.get("combo"), ensure_ascii=False)[:160])
print("  verdict:", json.dumps(j.get("verdict"), ensure_ascii=False)[:120])
tm = j.get("team")
print("  team   :", json.dumps(tm, ensure_ascii=False)[:400] if tm else tm)
print("\n  变体：")
for i, t in enumerate(j.get("variants") or [], 1):
    print(f"   [{i}] {t}")

print()
print("=" * 78)
print("② 本地降级路径的文案（同一输入，强制 ai_available=False）")
print("=" * 78)
import ai  # noqa: E402
import team_domain_copy as tdc  # noqa: E402

ai.ai_available = lambda: False
deg = tdc.generate_copy(form["shop_name"], form["scene"], form["extra"], "")
print("  降级单条：", deg)

print()
print("=" * 78)
print("③ 判定")
print("=" * 78)
live_variants = j.get("variants") or []
hit = any(deg.strip() and deg.strip() == (v or "").strip() for v in live_variants)
print(f"  线上变体与降级文本逐字相同？ {'是 → 判定为降级' if hit else '否'}")
print(f"  team 字段有效载荷？ {'有内容 → 走了多 agent' if tm and str(tm) not in ('{}', 'None', '[]') else '空 → 可能降级'}")

print()
print("=" * 78)
print("④ 最近 copy 域的 AI 调用记录（看有没有真的调模型）")
print("=" * 78)
calls = C.get("/api/metrics/ai/calls?limit=40").json()
rows = calls if isinstance(calls, list) else (calls.get("calls") or calls.get("items") or [])
copies = [x for x in rows if "copy" in str(x.get("domain"))]
if not copies:
    print("  ⚠️ 最近 40 次调用里没有 copy 域记录")
for x in copies[:10]:
    print(f"  {str(x.get('at') or x.get('created_at'))[:19]} ok={x.get('ok')} "
          f"domain={x.get('domain')} tokens={x.get('total_tokens')} "
          f"err={str(x.get('error') or '')[:50]}")
