"""把演示库从「最后一天」补到「今天」—— 只做加法，不删任何历史。

为什么需要它（两个真实场景）：

1. **同一月内隔了几天**：演示库停在 10/04，今天已经 10/10。首页「今日」是空的，
   掌柜复盘（读当月）也没内容。
   此时 `seed_demo_data.py --current-only` **会被幂等门挡住**（当月已有流水会拒绝执行，
   防止日销翻倍），而 `--force` 是"清空整库重灌"，会把历史月全丢掉 —— 都不合适。
2. **跨月**：演示库停在上个月，本月一笔都没有。这时 `--current-only` 是可用且推荐的
   （幂等门放行），它会顺带摊本月固定成本。

本脚本把上面两种情况一起解决：先看库里最后一天是哪天，再把生成器的窗口指向
「最后一天的次日 ~ 今天」，沿用**同一个确定性生成器**（同一天生成的数据与完整灌数一致），
只 INSERT、不 DELETE。

用法（在本目录的 server 下执行）：
    cd server
    python ..\\scripts\\topup_demo_data.py

补完会打印补了几天、多少笔，以及今日/本月的口径，便于当场核对。
"""
import sys
from datetime import date, timedelta
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import seed_demo_data as seed  # noqa: E402


def last_data_day() -> date | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(substr(created_at, 1, 10)) FROM transactions").fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def main() -> int:
    db.init_db()
    today = date.today()
    last = last_data_day()

    if last is None:
        print("库里没有任何流水 —— 请先跑一次完整灌数：")
        print("    python ..\\scripts\\seed_demo_data.py")
        return 1

    print(f"演示库现有数据：最后一天 {last}，今天 {today}")
    if last >= today:
        print("✅ 已经是最新（今天已有流水），无需补灌。")
        return 0

    start = last + timedelta(days=1)
    days = (today - start).days + 1
    print(f"补灌窗口：{start} ~ {today}（{days} 天）—— 只做加法，不动历史")

    have = {c["name"]: c["id"] for c in db.list_customers()
            if c["name"] in seed.CUSTOMER_BASKET}
    if not have:
        print("❌ 库里没有熟客档案，请先跑一次完整灌数（不带参数）。")
        return 1

    before = db.today_summary()
    # 把生成器的窗口指向缺失区间：同一个确定性生成器，同一天的数据与完整灌数一致
    seed.MONTH_START, seed.DAYS = start, days
    seed.seed_transactions(have)

    after_last = last_data_day()
    print(f"✅ 补灌完成：最后一天 {last} → {after_last}")
    month = db.monthly_summary()
    now = db.today_summary()
    print(f"   今日：收入 {now['income']:,.0f} / 支出 {now['expense']:,.0f}"
          f"（补灌前 {before['income']:,.0f} / {before['expense']:,.0f}）")
    print(f"   本月：收入 {month['income']:,.0f} / 支出 {month['expense']:,.0f}")
    print("   打开首页刷新一下就能看到「今日」有数了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
