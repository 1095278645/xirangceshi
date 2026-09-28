# -*- coding: utf-8 -*-
"""真实用户验证：从**真实部署**的库里导出一页验证报告（只读，不动数据）。

用途：把"用户用得怎么样"变成可填表的数字（配合 docs/user-validation-plan.md 的模板）。

指标：
  · 使用广度：营业天数、记账笔数、熟客数
  · 执行闭环：提醒送达率（sent_ok / 总数）
  · AI 使用：调用次数、按业务域分布、平均延迟
  · 经营结果：毛利率、保本日销、实际日销（若已建单店档案）

用法：
    cd server
    python ..\\scripts\\validation_report.py                 # 读演示库
    python ..\\scripts\\validation_report.py --db D:\\shop\\data\\ai_shopkeeper.db --days 14
    python ..\\scripts\\validation_report.py --out report.md
"""
import argparse
import sqlite3
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
DEFAULT_DB = SERVER / "data" / "ai_shopkeeper.db"


def ro(path: Path) -> sqlite3.Connection:
    """只读打开（不会写、不会触发迁移）。"""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def q1(con, sql, args=(), default=0):
    try:
        r = con.execute(sql, args).fetchone()
        return (r[0] if r and r[0] is not None else default)
    except sqlite3.Error:
        return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(f"✗ 找不到库文件：{path}")
        return 1
    days = f"-{max(1, args.days)} days"
    con = ro(path)
    try:
        months_win = q1(con, "SELECT COUNT(DISTINCT substr(created_at,1,7)) FROM transactions")
        days_active = q1(con, "SELECT COUNT(DISTINCT substr(created_at,1,10)) FROM transactions")
        txn = q1(con, "SELECT COUNT(*) FROM transactions")
        income = q1(con, "SELECT COALESCE(SUM(amount),0) FROM transactions "
                         "WHERE trans_type='income' AND COALESCE(status,'active')!='voided'")
        cost = q1(con, "SELECT COALESCE(SUM(amount),0) FROM transactions "
                       "WHERE trans_type='expense' AND category='进货' "
                       "AND COALESCE(status,'active')!='voided'")
        customers = q1(con, "SELECT COUNT(*) FROM customers")
        rem_total = q1(con, "SELECT COUNT(*) FROM reminders")
        rem_sent = q1(con, "SELECT COUNT(*) FROM reminders WHERE send_ok=1")
        ai_calls = q1(con, "SELECT COUNT(*) FROM ai_metrics")
        ai_tokens = q1(con, "SELECT COALESCE(SUM(total_tokens),0) FROM ai_metrics")
        ai_lat = q1(con, "SELECT COALESCE(AVG(latency_ms),0) FROM ai_metrics")
        by_domain = []
        try:
            by_domain = [(r["domain"] or "(未标注)", r["c"]) for r in con.execute(
                "SELECT domain, COUNT(*) AS c FROM ai_metrics GROUP BY domain "
                "ORDER BY c DESC LIMIT 6")]
        except sqlite3.Error:
            pass
        prof = None
        try:
            prof = con.execute("SELECT * FROM store_profiles ORDER BY id DESC LIMIT 1").fetchone()
        except sqlite3.Error:
            pass
        doy = q1(con, "SELECT COUNT(*) FROM transactions WHERE substr(created_at,1,10) >= date('now','localtime', ?)",
                 (days,))
    finally:
        con.close()

    margin = (income - cost) / income if income else None
    daily = income / days_active if days_active else None
    be = None
    if prof and margin:
        fixed = (prof["rent"] or 0) + (prof["salary"] or 0) + (prof["utilities"] or 0)
        be = fixed / margin / 30 if margin else None

    L = []
    L.append(f"# 用户验证报告 · {path.name}")
    L.append("")
    L.append(f"- 数据窗口：全部历史（近 {args.days} 天记账 {doy} 笔）")
    L.append(f"- 覆盖月份：{months_win} 个 · 有营业的天数：{days_active}")
    L.append("")
    L.append("## 使用广度")
    L.append(f"- 累计记账：**{txn}** 笔 · 累计收入：**{income:.0f}** 元")
    L.append(f"- 熟客档案：**{customers}** 位")
    L.append("")
    L.append("## 执行闭环")
    rate = f"{rem_sent / rem_total * 100:.0f}%" if rem_total else "—"
    L.append(f"- 熟客提醒：共 {rem_total} 条，已送达 {rem_sent} 条（送达率 **{rate}**）")
    L.append("")
    L.append("## AI 使用")
    L.append(f"- 调用次数：**{ai_calls}** · 累计 tokens：{ai_tokens} · 平均延迟：{ai_lat:.0f} ms")
    if by_domain:
        L.append("- 按业务域：" + "、".join(f"{d} {c} 次" for d, c in by_domain))
    L.append("")
    L.append("## 经营结果")
    L.append(f"- 毛利率：{'—' if margin is None else format(margin, '.1%')}")
    L.append(f"- 实际日销：{'—' if daily is None else f'{daily:.0f} 元'}")
    L.append(f"- 保本日销：{'—' if be is None else f'{be:.0f} 元'}"
             f"（{'高于保本线 ✅' if (be and daily and daily >= be) else '低于/未算保本线' if be else '未建单店档案'}）")
    L.append("")
    L.append("> 数字口径见 `docs/user-validation-plan.md`；本报告由 "
             "`scripts/validation_report.py` 只读导出，不改动任何数据。")

    text = "\n".join(L) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"已写出：{args.out}")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
