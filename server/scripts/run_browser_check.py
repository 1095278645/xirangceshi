# -*- coding: utf-8 -*-
"""run_browser_check.py — 用真实浏览器实测网页端（自带隔离环境）

做的事情：
  1. 把整个 server/ 目录**复制**一份到临时目录（排除 data/.venv/__pycache__），
     再把演示库用 SQLite backup API 复制进副本的 data/（WAL 也复制得对）
  2. 用副本起一个后端（端口 8020）—— 副本的 config.BASE_DIR 指向临时目录，
     所以它读写的是副本数据，**不碰真实演示库**
  3. 调 scripts/check_web_click.js：起 Edge 无头浏览器，真点五个新页面
  4. 结束后杀后端、删临时目录
  5. 打印真实演示库的关键计数，确认它没被改过

为什么不改环境变量指定数据目录：config.py 里 DATA_DIR 是由 __file__ 推导的常量，
环境变量改不了它（我第一版就是这么想的，等于没隔离）。复制整棵树最稳妥。

之所以必须隔离：浏览器实测会真的建店铺、发推送、生成收款单 —— 直接打在演示库上
就会把准备好的演示数据弄脏（前几轮已踩过两次，清理脚本都写了两版）。

用法：cd server && python scripts/run_browser_check.py
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent          # 真实 server 目录
PORT = 8020
BASE = f"http://127.0.0.1:{PORT}"
REAL_DB = SERVER / "data" / "ai_shopkeeper.db"
CHECK_JS_REL = "scripts/check_web_click.js"

# 演示库的基准状态，实测跑完后真实库必须原样
BASELINE = ("transactions", "customers", "memories", "payment_collections")

SKIP_DIRS = {"data", ".venv", "__pycache__", ".pytest_cache", ".git", "node_modules"}


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
    """用 SQLite backup API 复制，确保 WAL 里的内容也一起带过去。"""
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
            shutil.copytree(item, target,
                            ignore=shutil.ignore_patterns(*SKIP_DIRS))
        else:
            shutil.copyfile(item, target)


def port_in_use(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port_free(port: int, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_in_use(port):
            return True
        time.sleep(0.4)
    return False


def kill_tree(proc) -> None:
    """杀掉整个进程树。

    只 terminate uvicorn 会留下它的子进程继续占着端口 —— 实测过一个 23:29 起的
    残留 python 一直监听 8020，导致后面几轮"实测"其实连的是**旧进程**（旧代码、
    旧数据副本），于是出现"改了代码却没生效""恢复没覆盖"这类假失败，
    白白排查了很久。所以这里用 taskkill /T 连子树一起杀，并等端口真正释放。
    """
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=15)
    except Exception:  # noqa: BLE001  非 Windows 或 taskkill 不存在
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_health(proc, timeout=45) -> tuple[bool, str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return True, ""
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    out = ""
    if proc.stdout:
        try:
            proc.terminate()
            out = proc.stdout.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
    return False, out


def main() -> int:
    before = snapshot_counts(REAL_DB)
    print("真实演示库（实测前）：", before)

    # 端口必须先空出来：否则健康检查会打到**别的进程**上，
    # 后面的断言全都在验一个旧服务（实测踩过，见 kill_tree 注释）
    if port_in_use(PORT):
        print(f"❌ 端口 {PORT} 已被占用，无法保证实测的是当前代码。")
        print("   请先关掉占用它的进程（或换端口）再跑：")
        print(f"   netstat -ano | findstr :{PORT}")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="browser-check-"))
    srv = tmp / "server"
    print(f"复制 server/ → {srv}")
    copy_tree(SERVER, srv)
    copy_db(REAL_DB, srv / "data" / "ai_shopkeeper.db")
    reg = SERVER / "data" / "registry.db"
    if reg.exists():
        shutil.copyfile(reg, srv / "data" / "registry.db")
    (srv / "data" / "backups").mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-m", "uvicorn", "main:app",
           "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"]
    print(f"\n启动隔离后端：{BASE}")
    log_path = srv / "uvicorn.log"
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(srv),
                            stdout=log_file, stderr=subprocess.STDOUT)
    rc = 1
    try:
        ok, out = wait_health(proc)
        if not ok:
            print("后端启动失败：\n" + out[:3000])
            return 1
        print("后端就绪（读的是副本数据）\n")

        node = shutil.which("node") or "node"
        r = subprocess.run([node, str(srv / CHECK_JS_REL)], cwd=str(srv),
                           env={**dict(__import__("os").environ), "BASE_URL": BASE})
        rc = r.returncode
    finally:
        kill_tree(proc)
        log_file.close()
        if not wait_port_free(PORT):
            print(f"⚠️ 端口 {PORT} 在结束后仍被占用，下一轮实测可能连到旧进程")

    # 把后端的异常/Traceback 打出来 —— 浏览器侧只看到 500，真正原因在这里
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        if "Traceback" in text or "ERROR" in text:
            print("\n--- 隔离后端日志里的错误 ---")
            keep = []
            lines = text.splitlines()
            for i, ln in enumerate(lines):
                if "Traceback" in ln or "ERROR" in ln:
                    keep.extend(lines[i:i + 25])
                    keep.append("  ...")
            print("\n".join(keep[:120]))
    except Exception:  # noqa: BLE001
        pass

    after = snapshot_counts(REAL_DB)
    print("\n真实演示库（实测后）：", after)
    if before == after:
        print("✅ 真实演示库未被改动（浏览器实测跑在隔离副本上）")
    else:
        print("⚠️ 真实演示库发生了变化：")
        for k in before:
            if before.get(k) != after.get(k):
                print(f"   {k}: {before.get(k)} → {after.get(k)}")
        rc = rc or 1

    shutil.rmtree(tmp, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
