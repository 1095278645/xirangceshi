"""预热演示缓存：让经营洞察与报税建议在现场秒出。

做两件事（都走**统一洞察入口** `/api/insights`，会真调 AI，共约 30~90 秒）：
  1. 经营洞察 —— scene=monthly（账本页一打开就请求它）
  2. 报税建议 —— scene=tax（点「算增值税」后请求）

用法：
    cd server
    python ..\\scripts\\prewarm_cache.py            # 预热默认档位
    python ..\\scripts\\prewarm_cache.py 350000 400000   # 指定要预热的销售额档位
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER))

BASE = "http://127.0.0.1:8000"
# 默认预热演示使用的销售额档位（教程第 4 站用 350000）
DEFAULT_REVENUES = [350000]


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    revenues = [int(x) for x in sys.argv[1:]] or DEFAULT_REVENUES
    today = date.today()
    print(f"预热缓存（后端 {BASE}）")
    print()

    # ---- 1. 经营洞察 ----
    print(f"[1/2] 经营洞察 {today.year}-{today.month:02d} …")
    t0 = time.time()
    try:
        r = post("/api/insights", {"scene": "monthly",
                                   "payload": {"year": today.year, "month": today.month},
                                   "refresh": True})
        dt = time.time() - t0
        text = str(r.get("insights", ""))
        print(f"      ✅ 生成完成 {dt:.1f}s  cached={r.get('cached')}  {len(text)} 字")
        print(f"      {text.replace(chr(10), ' ')[:70]}")
    except urllib.error.HTTPError as e:
        print(f"      ❌ HTTP {e.code}（后端是否已启动？）")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"      ❌ {type(e).__name__}: {e}")
        print("      请确认后端已启动：python -m uvicorn main:app --port 8000")
        return 1

    # ---- 2. 报税建议 ----
    print()
    for rev in revenues:
        print(f"[2/2] 报税建议 季度销售额 {rev:,} …")
        t0 = time.time()
        try:
            r = post("/api/insights", {"scene": "tax",
                                       "payload": {"quarterly_revenue": rev},
                                       "refresh": True})
            dt = time.time() - t0
            text = str(r.get("advice", ""))
            print(f"      ✅ 生成完成 {dt:.1f}s  cached={r.get('cached')}  {len(text)} 字")
            print(f"      {text.replace(chr(10), ' ')[:70]}")
        except Exception as e:  # noqa: BLE001
            print(f"      ❌ {type(e).__name__}: {e}")

    print()
    print("预热完成。现在现场打开账本页 / 点「算增值税」都会秒出。")
    print("（若演示中改了数据，需点卡片上的「重新分析」/「重新生成」）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
