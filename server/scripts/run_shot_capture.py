# -*- coding: utf-8 -*-
"""run_shot_capture.py — 用「演示库副本 + 隔离后端」跑一遍截图

为什么要隔离：截图动线里有**写操作**（记一笔、生成收款码、建二号店）。
直接打在真实演示库上，会把准备好的演示数据弄脏（多的收款单、`二号店`、
一条测试流水会出现在评审看到的界面上）。这里复制整棵 server/ 起一个临时后端，
截完即弃。

用法：
    cd server
    python scripts/run_shot_capture.py                 # 截到仓库 deliverables/video/shots
    python scripts/run_shot_capture.py --out D:\\材料\\02-系统演示视频\\shots
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
PORT = 8021
SKIP_DIRS = {"data", ".venv", "__pycache__", ".pytest_cache", ".git", "node_modules"}
# 演示库的基准计数：跑完必须原样（截图不该碰真实库）
BASELINE = ("transactions", "customers", "memories", "payment_collections")


def snapshot_counts(db: Path) -> dict:
    if not db.exists():
        return {}
    conn = sqlite3.connect(str(db))
    try:
        out = {}
        for t in BASELINE:
            try:
                out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = -1
        return out
    finally:
        conn.close()


def copy_db(src: Path, dst: Path) -> None:
    """SQLite backup API：连 WAL 里未落盘的内容一起复制。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in SKIP_DIRS:
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns(*SKIP_DIRS))
        else:
            shutil.copyfile(item, target)


def port_in_use(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port_free(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_in_use(port):
            return True
        time.sleep(0.4)
    return False


def kill_tree(proc) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=15)
    except Exception:  # noqa: BLE001
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(SERVER.parent / "deliverables" / "video" / "shots"),
                    help="截图输出目录")
    ap.add_argument("--only", default="",
                    help="只重拍指定屏（逗号分隔的文件名），留空 = 全部")
    args = ap.parse_args()

    real_db = SERVER / "data" / "ai_shopkeeper.db"
    before = snapshot_counts(real_db)
    print("真实演示库（截图前）：", before)

    if port_in_use(PORT):
        print(f"❌ 端口 {PORT} 已被占用，无法保证截的是当前代码。")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="shot-capture-"))
    srv = tmp / "server"
    print(f"复制 server/ → {srv}")
    copy_tree(SERVER, srv)
    copy_db(real_db, srv / "data" / "ai_shopkeeper.db")
    reg = SERVER / "data" / "registry.db"
    if reg.exists():
        shutil.copyfile(reg, srv / "data" / "registry.db")
    (srv / "data" / "backups").mkdir(parents=True, exist_ok=True)

    env = {**os.environ, "SHOP_API_PROFILE": "full"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", str(PORT), "--log-level", "warning"],
        cwd=str(srv), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    rc = 1
    try:
        base = f"http://127.0.0.1:{PORT}"
        deadline = time.time() + 60
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                    if r.status == 200:
                        ready = True
                        break
            except Exception:  # noqa: BLE001
                time.sleep(0.4)
        if not ready:
            print("❌ 隔离后端启动失败")
            return 1
        print(f"隔离后端就绪：{base}（读的是副本数据）\n")

        node = shutil.which("node") or "node"
        r = subprocess.run([node, str(srv / "scripts" / "capture_shots.js")],
                           cwd=str(srv),
                           env={**env, "BASE_URL": base, "SHOT_DIR": str(Path(args.out)),
                                "SHOT_ONLY": args.only})
        rc = r.returncode
    finally:
        kill_tree(proc)
        if not wait_port_free(PORT):
            print(f"⚠️ 端口 {PORT} 结束后仍被占用")
        shutil.rmtree(tmp, ignore_errors=True)

    after = snapshot_counts(real_db)
    print("\n真实演示库（截图后）：", after)
    if before == after:
        print("✅ 真实演示库未被改动（截图跑在隔离副本上）")
    else:
        print("⚠️ 真实演示库发生变化：")
        for k in before:
            if before.get(k) != after.get(k):
                print(f"   {k}: {before.get(k)} → {after.get(k)}")
        rc = rc or 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
