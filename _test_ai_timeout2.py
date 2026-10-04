# -*- coding: utf-8 -*-
"""量化 AI 超时的真实上界：黑洞服务 + 统计连接次数，确认没有隐藏重试。

修复前：OpenAI SDK 默认 600s + 2 次重试 → 用户等十几分钟（"没反应"）。
修复后：按 SHOP_AI_TIMEOUT 到点抛异常，调用方走规则兜底。
"""
import os
import socket
import sys
import threading
import time
from pathlib import Path

SERVER = Path(r"C:\Users\Administrator\Desktop\xirang\server")
sys.path.insert(0, str(SERVER))

T = sys.argv[1] if len(sys.argv) > 1 else "3"
os.environ["SHOP_AI_TIMEOUT"] = T
os.environ["SHOP_AI_MAX_RETRIES"] = "0"

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0))
srv.listen(16)
port = srv.getsockname()[1]
held, stop = [], threading.Event()


def accept_loop():
    while not stop.is_set():
        try:
            srv.settimeout(0.3)
            c, _ = srv.accept()
            held.append(c)
        except OSError:
            continue


threading.Thread(target=accept_loop, daemon=True).start()

import config  # noqa: E402
import ai  # noqa: E402

ai.load_settings = lambda: {"api_key": "sk-test", "base_url": f"http://127.0.0.1:{port}/v1",
                            "model": "deepseek-v4.1-flash", "ai_pipeline": "team",
                            "api_profile": "full"}

n0 = len(held)
t0 = time.perf_counter()
err = None
try:
    ai.chat([{"role": "user", "content": "写一句文案"}], max_tokens=64, domain="探测·超时")
except Exception as e:  # noqa: BLE001
    err = e
dt = time.perf_counter() - t0
attempts = len(held) - n0

print(f"AI_TIMEOUT={config.AI_TIMEOUT}s  AI_MAX_RETRIES={config.AI_MAX_RETRIES}")
print(f"异常：{type(err).__name__ if err else '（无，竟成功）'}  {str(err)[:80]}")
print(f"实际耗时 {dt:.1f}s ｜ 黑洞收到的连接数（≈重试次数）{attempts}")
print(f"倍数 = {dt / float(T):.1f}x")
print(f"→ 默认 90s 时的预估上界 ≈ {90 * dt / float(T):.0f}s")
print(f"→ 原默认 600s×3 次重试的上界 ≈ 1800s（30 分钟）")

stop.set()
srv.close()
for c in held:
    try:
        c.close()
    except Exception:  # noqa: BLE001
        pass
