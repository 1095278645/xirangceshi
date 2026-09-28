# -*- coding: utf-8 -*-
"""恢复演练：验证"数据丢了能回来"的完整路径（云化第一步，评审必问）。

流程（全部走项目自己的机制，不依赖任何云服务）：
  1. VACUUM INTO 生成一致性快照（= 线上每 6 小时自动在做的事）
  2. 记录关键计数（库文件直读，不经过 ORM/缓存）
  3. **模拟灾难**：删除当前库文件（含 -wal/-shm）
  4. 用快照恢复（`backup.restore_from_file`，恢复前还会自动再留一份）
  5. 校验：库可打开、表齐全、关键计数与灾难前一致

用法：
    cd server
    python ..\\scripts\\restore_drill.py             # 在**临时副本**上演练（默认，不动演示库）
    python ..\\scripts\\restore_drill.py --inplace   # 直接在真实库上演练（会真删真恢复，慎用）
    python ..\\scripts\\restore_drill.py --db server/data/ai_shopkeeper.db
"""
import argparse
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

import backup  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402

TABLES = ("transactions", "customers", "vouchers")


def counts(path: Path) -> dict:
    """库文件直读计数（绕过模块缓存，验证的是磁盘上的真实文件）。"""
    con = sqlite3.connect(str(path))
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        out = {"tables": len(names)}
        for t in TABLES:
            out[t] = (con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      if t in names else None)
        return out
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(SERVER / "data" / "ai_shopkeeper.db"))
    ap.add_argument("--inplace", action="store_true",
                    help="直接在 --db 上演练（默认为临时副本）")
    ap.add_argument("--keep", action="store_true", help="保留临时目录便于排查")
    args = ap.parse_args()

    src = Path(args.db)
    if not src.exists():
        print(f"✗ 找不到库文件：{src}")
        return 1

    tmp = None
    if args.inplace:
        work = src
        workdir = src.parent
    else:
        tmp = tempfile.TemporaryDirectory()
        workdir = Path(tmp.name)
        work = workdir / src.name
        shutil.copy(src, work)
        print(f"（演练在临时副本上：{work}）")

    config.DATA_DIR = str(workdir)
    config.DB_PATH = str(work)
    db.DB_PATH = Path(config.DB_PATH)
    db._schema_ready.clear()
    db.init_db()

    before = counts(work)
    print("① 灾难前计数：", before)

    print("② 生成一致性快照（VACUUM INTO）…")
    info = backup.create_backup("drill", note="恢复演练")
    snap = Path(info["path"])
    print(f"   快照：{snap.name}（{info['size'] / 1024:.1f} KB）")

    print("③ 模拟灾难：删除当前库（含 -wal/-shm）…")
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(work) + suffix)
        if p.exists():
            p.unlink()
    if work.exists():
        print("✗ 库文件未被删除，演练中止")
        return 1

    print("④ 从快照恢复…")
    backup.restore_from_file(snap, auto_snapshot=True)

    after = counts(work)
    print("⑤ 恢复后计数：", after)

    ok = (work.exists() and after["tables"] >= before["tables"]
          and all(after.get(t) == before.get(t) for t in TABLES))
    if ok:
        print("✅ 恢复演练通过：库可打开、表齐全、关键计数与灾难前一致")
    else:
        print("✗ 恢复演练失败：恢复后与灾难前不一致，请检查 backup/restore 逻辑")

    if tmp and not args.keep:
        tmp.cleanup()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
