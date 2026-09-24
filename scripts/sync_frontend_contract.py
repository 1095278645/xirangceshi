# -*- coding: utf-8 -*-
"""把双前端共享契约从真源同步到两处副本（真源 → H5 / 小程序）。

用法（仓库根）：python scripts/sync_frontend_contract.py
副本会被加上 AUTO-GENERATED 头；请只改真源：
  shared/frontend_contract.js
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "shared" / "frontend_contract.js"
TARGETS = [
    ROOT / "server" / "static" / "shared" / "frontend_contract.js",
    ROOT / "miniprogram" / "shared" / "frontend_contract.js",
]

MARK = ("/* AUTO-GENERATED from shared/frontend_contract.js —— 请勿手改；"
        "改真源后跑 scripts/sync_frontend_contract.py */\n")


def main() -> int:
    body = SRC.read_text(encoding="utf-8")
    for t in TARGETS:
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(MARK + body, encoding="utf-8", newline="\n")
        print(f"synced -> {t.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
