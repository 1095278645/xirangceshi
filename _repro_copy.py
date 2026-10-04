# -*- coding: utf-8 -*-
"""复现「AI 生成文案没反应」：按前端完全一样的调用打线上。"""
import json
import os
import time
import urllib3

import httpx

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
B = os.environ.get("XIRANG_BASE", "https://124.222.74.116").rstrip("/")
C = httpx.Client(base_url=B, headers={"X-Shop-Token": os.environ["XIRANG_TOKEN"]},
                 timeout=300, verify=False)

# 前端 state.copyForm 的字段（copy.js 渲染时用的就是这四个）
payload = {"shop_name": "巷口早餐铺", "scene": "今天新到土鸡蛋", "extra": "", "customer_name": "王阿姨"}

print("=== POST /api/insights  scene=copy（前端原样调用）===")
t0 = time.time()
try:
    r = C.post("/api/insights", json={"scene": "copy", "payload": payload})
    dt = time.time() - t0
    print(f"  HTTP {r.status_code}  耗时 {dt:.1f}s")
    j = r.json()
    print("  字段:", sorted(j.keys()))
    print("  text 长度:", len(str(j.get("text") or "")))
    print("  variants:", len(j.get("variants") or []))
    print("  gene_id:", j.get("gene_id"))
    print("  正文预览:", str(j.get("text"))[:160].replace("\n", " "))
except Exception as e:  # noqa: BLE001
    print(f"  ❌ {type(e).__name__}: {e}  耗时 {time.time()-t0:.1f}s")

print("\n=== 对照：其它 scene 是否正常 ===")
for scene in ("monthly", "growth"):
    t0 = time.time()
    try:
        r = C.post("/api/insights", json={"scene": scene, "payload": {}, "refresh": True})
        print(f"  {scene}: HTTP {r.status_code} {time.time()-t0:.1f}s  "
              f"{str(r.json())[:100]}")
    except Exception as e:  # noqa: BLE001
        print(f"  {scene}: ❌ {type(e).__name__} {time.time()-t0:.1f}s")

print("\n=== 对照：健康与 AI 探活 ===")
print("  /api/health   :", C.get("/api/health").json())
print("  /api/health/ai:", C.get("/api/health/ai").json())

print("\n=== 最近 AI 调用（看 copy 域是否报错）===")
try:
    calls = C.get("/api/metrics/ai/calls?limit=12").json()
    rows = calls if isinstance(calls, list) else (calls.get("calls") or calls.get("items") or [])
    for x in rows[:12]:
        print(f"  {str(x.get('at') or x.get('created_at'))[:19]} ok={x.get('ok')} "
              f"domain={x.get('domain')} err={str(x.get('error') or '')[:60]}")
except Exception as e:  # noqa: BLE001
    print("  取调用明细失败:", e)
