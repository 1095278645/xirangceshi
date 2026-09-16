"""生成「演示用的真实感经营数据」。

设计要点（为什么这样造）：

1. **交易模型**：一笔交易 = 一位顾客买 1~N 件商品，金额由菜单价目表累加得到。
   早期版本按"每天 6 笔、凑够日营业额"倒推，必然推出 200 元级客单价 ——
   对早餐铺完全不合常理。现在按真实菜单与篮子分布生成，客单价落在 5~9 元。

2. **期间**：当月 1 日 ~ 今天（不是"最近 N 天"）。
   房租/水电是**整月**固定成本，只灌半个月会让固定开销压在半个月的现金里，
   经营洞察的现金视角会说"白忙活"，与单店模型的月利润口径打架。

3. **口径自洽**：脚本末尾会核验
   - 客单价是否落在餐饮合理区间
   - 月度现金结余与单店模型月利润是否量级一致
   - 反推日销是否高于保本线（否则演示会显示「危险」）

4. **幂等保护**：重复运行会拒绝执行（流水翻倍会让日销失真），
   需要重灌时加 --force，或先删库。

用法：
    cd server
    Remove-Item data\\ai_shopkeeper.db -ErrorAction SilentlyContinue
    python ..\\scripts\\seed_demo_data.py
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER))

import db  # noqa: E402

TODAY = date.today()
MONTH_START = TODAY.replace(day=1)
DAYS = (TODAY - MONTH_START).days + 1

# ---------------- 菜单（早餐铺真实价位） ----------------
# (名称, 单价, 出现权重)
MENU = [
    ("肉包", 2, 20), ("菜包", 2, 10), ("豆浆", 2, 22), ("油条", 2, 14),
    ("茶叶蛋", 1.5, 8), ("白粥", 2, 6), ("豆腐脑", 3, 9), ("小笼包", 6, 12),
    ("馄饨", 8, 7), ("煎饼", 6, 9), ("咸菜", 1, 5), ("八宝粥", 3, 5),
]
# 篮子大小分布：多数人买 1~2 件，少数家庭单买 4 件以上
BASKET_WEIGHTS = {1: 30, 2: 40, 3: 18, 4: 8, 5: 3, 6: 1}
# 时段分布：(起始小时, 结束小时, 顾客量权重)  —— 早餐双高峰
HOUR_BANDS = [(6, 7, 25), (7, 8, 40), (8, 9, 22), (9, 10, 8), (17, 18, 5)]

# ---------------- 成本 ----------------
# 毛利率 = 1 - 直接成本率 → 约 60%，与单店模型演示参数一致
DIRECT_COST_RATIO = 0.38
# 日常杂费（耗材等，不计入演示口径里的「固定成本 8000」）
MISC_COST_RATIO = 0.05
RENT = 6000               # 月房租
UTILITIES = 2000          # 月水电

# ---------------- 10 位熟客（演示叙事的数据源） ----------------
CUSTOMERS = [
    {"name": "王阿姨", "tags": "老主顾,早餐常客", "favorite": "肉包,豆浆",
     "memories": ["孙子今年考上了一中，她说起来特别自豪",
                  "胃不太好，豆浆要热的、别太满"]},
    {"name": "李叔", "tags": "附近工地", "favorite": "肉包,茶叶蛋",
     "memories": ["上个月说工地要搬到城东，可能来得少了"]},
    {"name": "张叔", "tags": "每天都来", "favorite": "油条,豆腐脑",
     "memories": ["老伴住院了，最近一个人吃饭"]},
    {"name": "刘姐", "tags": "带娃,上班族", "favorite": "小笼包,豆浆",
     "memories": ["女儿刚上幼儿园，早上要赶时间", "说我们家的辣椒酱好吃"]},
    {"name": "陈伯", "tags": "退休", "favorite": "白粥,咸菜",
     "memories": []},
    {"name": "赵姐", "tags": "对面理发店", "favorite": "豆浆",
     "memories": ["店里的小工也爱喝豆浆，常一起买"]},
    {"name": "小周", "tags": "外卖骑手", "favorite": "煎饼",
     "memories": ["赶时间，通常打包带走", "说过我们家出餐快"]},
    {"name": "孙奶奶", "tags": "小区老住户", "favorite": "肉包",
     "memories": ["腿脚不便，有时候让我给送到楼下"]},
    {"name": "老郑", "tags": "棋友", "favorite": "馄饨",
     "memories": ["每天早上下棋前先来吃一碗"]},
    {"name": "吴姐", "tags": "新客,回头客", "favorite": "八宝粥,菜包",
     "memories": ["上礼拜第一次来，说豆浆比别家浓"]},
]
# 每位熟客的固定点单（金额由价目表计算，保证「常点」与消费记录一致）
CUSTOMER_BASKET = {
    "王阿姨": ["肉包", "肉包", "豆浆"], "李叔": ["肉包", "肉包", "茶叶蛋"],
    "张叔": ["油条", "豆腐脑"], "刘姐": ["小笼包", "豆浆"],
    "陈伯": ["白粥", "咸菜"], "赵姐": ["豆浆", "豆浆"],
    "小周": ["煎饼", "豆浆"], "孙奶奶": ["肉包", "菜包", "豆浆"],
    "老郑": ["馄饨"], "吴姐": ["八宝粥", "菜包"],
}

INCOME_CATEGORY = "主营业务收入"
# 支出分类**必须用 CATEGORY_TO_ACCOUNTS 里登记的规范名**，否则：
#   1) 账本页显示的品类是自造名（与科目表对不上）
#   2) 自动凭证会落到兜底科目 —— 实测「房租」会被记成「管理费用-办公费」
# 规范名对照：房租 → 租赁及物业费；水电费 → 租赁及物业费；
#             工资 → 职工薪酬；日常耗材 → 办公费
COST_ITEMS = [("面粉和猪肉", "进货"), ("大豆和食用油", "进货"), ("蔬菜和调料", "进货"),
              ("一次性餐具和包装", "进货"), ("冷冻半成品", "进货"), ("鸡蛋和豆制品", "进货")]
MISC_ITEMS = [("清洁用品", "办公费"), ("餐巾纸和打包袋", "办公费"),
              ("一次性手套", "办公费"), ("洗洁精和抹布", "办公费")]
FIXED_ITEMS = [("门店房租", "租赁及物业费"), ("水电杂费", "租赁及物业费")]

PRICE = {name: price for name, price, _w in MENU}
WEIGHTS = [w for _n, _p, w in MENU]


def basket_price(items) -> float:
    return round(sum(PRICE[i] for i in items), 2)


def basket_desc(items) -> str:
    """把点单描述成店主会说的话，如「肉包两个、豆浆一杯」。"""
    from collections import Counter
    units = {"肉包": "个", "菜包": "个", "豆浆": "杯", "油条": "根", "茶叶蛋": "个",
             "白粥": "碗", "豆腐脑": "碗", "小笼包": "笼", "馄饨": "碗", "煎饼": "个",
             "咸菜": "碟", "八宝粥": "碗"}
    num = {1: "一", 2: "两", 3: "三", 4: "四", 5: "五", 6: "六"}
    parts = []
    for name, cnt in Counter(items).items():
        unit = units.get(name, "个")
        parts.append(f"{name}{num.get(cnt, cnt)}{unit}")
    return "、".join(parts)


def _seed_value(d: date) -> int:
    return int(d.strftime("%Y%m%d"))


def _backdate(txn_id, d: date, hour: int, minute: int):
    with db.get_conn() as conn:
        conn.execute("UPDATE transactions SET created_at=? WHERE id=?",
                     (f"{d.isoformat()} {hour:02d}:{minute:02d}:00", txn_id))


def _assert_categories_valid():
    """写入前校验分类名都在科目映射表里。

    为什么必须卡这一关：直接写库（本脚本）不经过记账接口的
    is_known_category 兜底修正，无效分类会被静默落到兜底科目 ——
    实测「房租」会被自动凭证记成「管理费用-办公费」，账本页与凭证一起错。
    """
    from categories import CATEGORY_TO_ACCOUNTS

    used = {INCOME_CATEGORY}
    used |= {c for _i, c in COST_ITEMS}
    used |= {c for _i, c in MISC_ITEMS}
    used |= {c for _i, c in FIXED_ITEMS}
    bad = sorted(c for c in used if c not in CATEGORY_TO_ACCOUNTS)
    if bad:
        raise SystemExit(
            f"❌ 分类名无效（不在科目映射表中）：{bad}\n"
            f"   可用分类：{sorted(CATEGORY_TO_ACCOUNTS)}\n"
            f"   否则账本品类与自动凭证都会落到兜底科目。")
    print(f"  分类校验通过（{len(used)} 个分类均在科目映射表中）")


def _guard_repeat():
    """幂等保护：已有当月流水时拒绝重复灌入（否则流水翻倍、日销失真）。"""
    with db.get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE substr(created_at,1,7)=?",
            (TODAY.strftime("%Y-%m"),)).fetchone()[0]
    if n and "--force" not in sys.argv:
        print(f"⚠ 本月已有 {n} 笔流水，重复灌入会让数据翻倍、日销失真。")
        print("  如需重灌：先删除 data/ai_shopkeeper.db，或加 --force 参数。")
        return False
    return True


def seed_customers():
    ids = {}
    for c in CUSTOMERS:
        cid, _created = db.find_or_create_customer(
            c["name"], tags=c["tags"], favorite=c["favorite"])
        ids[c["name"]] = cid
        for m in c["memories"]:
            db.add_memory(cid, m)
    print(f"  熟客 {len(ids)} 位，记忆点 {sum(len(c['memories']) for c in CUSTOMERS)} 条")
    return ids


def seed_transactions(ids):
    """按「菜单 + 篮子 + 时段」生成当月流水。"""
    created = 0
    last_visit = {name: None for name in ids}
    # 每位熟客每天是否到店：约 45% 的营业日会出现，保证光顾次数有差异
    cust_days = {name: set() for name in ids}

    for i in range(DAYS):
        d = MONTH_START + timedelta(days=i)
        rng = random.Random(_seed_value(d))
        daily_income = 0.0

        # ---- 熟客：每天挑 4~6 位到店，用固定点单（保证「常点」与记录一致）----
        visiting = rng.sample(CUSTOMERS, rng.randint(4, 6))
        for cust in visiting:
            items = CUSTOMER_BASKET[cust["name"]]
            amt = basket_price(items)
            hour, minute = rng.choice([(7, rng.randint(0, 59)), (8, rng.randint(0, 59))])
            txn_id, _ = db.add_transaction(
                ids[cust["name"]], basket_desc(items), amt, "income",
                INCOME_CATEGORY, note=f"[演示] {cust['name']}")
            _backdate(txn_id, d, hour, minute)
            created += 1
            daily_income += amt
            cust_days[cust["name"]].add(d)
            if last_visit[cust["name"]] is None or d > last_visit[cust["name"]]:
                last_visit[cust["name"]] = d

        # ---- 散客：按日营业额目标生成，直到凑够（客单价由菜单自然决定）----
        target = 1300 + (_seed_value(d) % 101)         # 1300~1400
        remaining = max(target - daily_income, 0)
        guard = 0
        while remaining > 0 and guard < 900:
            guard += 1
            # 按篮子大小抽点单
            size = rng.choices(list(BASKET_WEIGHTS), weights=list(BASKET_WEIGHTS.values()))[0]
            items = rng.choices([m[0] for m in MENU], weights=WEIGHTS, k=size)
            amt = basket_price(items)
            # 只跳过会大幅超目标的点单；容忍小幅超出，否则最后一截永远凑不满、
            # 日销会系统性偏低（实测偏低约 20%，导致单店模型判定成「临界」）
            if remaining < 12 and amt > remaining + 4:
                continue
            hour = rng.choices([b[0] for b in HOUR_BANDS],
                               weights=[b[2] for b in HOUR_BANDS])[0]
            txn_id, _ = db.add_transaction(
                None, basket_desc(items), amt, "income", INCOME_CATEGORY,
                note="[演示] 散客")
            _backdate(txn_id, d, hour, rng.randint(0, 59))
            created += 1
            remaining -= amt
            daily_income += amt

        # ---- 进货（直接成本，按当日收入比例）----
        cost_amt = round(daily_income * DIRECT_COST_RATIO, 2)
        item, cat = COST_ITEMS[_seed_value(d) % len(COST_ITEMS)]
        txn_id, _ = db.add_transaction(None, item, cost_amt, "expense", cat,
                                       note="[演示] 备货")
        _backdate(txn_id, d, 6, 0)
        created += 1

        # ---- 日常杂费 ----
        item2, cat2 = MISC_ITEMS[_seed_value(d) % len(MISC_ITEMS)]
        txn_id, _ = db.add_transaction(
            None, item2, round(daily_income * MISC_COST_RATIO, 2), "expense", cat2,
            note="[演示] 日常开支")
        _backdate(txn_id, d, 18, 30)
        created += 1

    # ---- 固定成本：房租月初、水电月中（都落在当月内）----
    mid = MONTH_START + timedelta(days=min(14, max(DAYS - 1, 0)))
    for (item, cat), day in zip(FIXED_ITEMS, (MONTH_START, mid)):
        amt = RENT if day == MONTH_START else UTILITIES
        txn_id, _ = db.add_transaction(None, item, amt, "expense", cat,
                                       note="[演示] 固定成本")
        _backdate(txn_id, day, 9, 0)
        created += 1

    # ---- 回填 last_visit（列表按它排序）----
    with db.get_conn() as conn:
        for name, d in last_visit.items():
            fallback = TODAY - timedelta(days=(len(name) % 5) + 1)
            conn.execute("UPDATE customers SET last_visit=? WHERE id=?",
                         ((d or fallback).isoformat() + " 08:30:00", ids[name]))

    print(f"  流水 {created} 笔")
    return created


def seed_reminders(ids):
    items = [
        ("王阿姨", "她孙子考上高中了，见面问问适应不适应"),
        ("张叔", "老伴住院，问一句恢复得怎么样"),
        ("李叔", "工地要搬走了，问问他以后还来不来"),
    ]
    for name, content in items:
        db.add_reminder(ids[name], content)
    print(f"  今日提醒 {len(items)} 条")


def report():
    """核验：演示现场会用到的每个数字都检查一遍，不自洽就报出来。"""
    import store

    stats = db.store_ledger_stats(TODAY.year, TODAY.month)
    monthly = db.monthly_summary()
    customers = db.list_customers()

    print()
    print("=" * 74)
    print("核验")
    print("=" * 74)

    # --- 客单价：最关键的真实性指标 ---
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM transactions "
            "WHERE trans_type='income'").fetchone()
    cnt, total = int(row["c"]), float(row["s"])
    avg = total / cnt if cnt else 0
    print(f"  收入 {total:,.0f} 元 / {cnt} 笔  客单价 {avg:,.1f} 元", end="")
    if 5 <= avg <= 12:
        print("  ✅ 餐饮合理区间")
    else:
        print(f"  ❌ 偏离餐饮合理区间(5~12)，早餐铺不应出现这种客单价")

    # --- 单店模型 ---
    gm = stats["gross_margin"]
    print(f"  反推：日销 {stats['daily_revenue']:,.0f} 元 / "
          f"营业 {stats['active_days']} 天 / "
          f"毛利率 {'—' if gm is None else format(gm, '.1%')}")

    m = None
    if gm is not None:
        r = store.calc_store_model(
            daily_revenue=stats["daily_revenue"], gross_margin=gm,
            rent=RENT, salary=8000, utilities=UTILITIES,
            total_investment=180000, cash_on_hand=50000, biz_type="餐饮")
        m = r["model"]
        print(f"  单店模型（房租{RENT}/人工8000/水电{UTILITIES}）："
              f"保本日销 {m['break_even_day']:,.1f} / "
              f"目标 {m['target_day']:,.1f} / 月利润 {m['month_profit']:,.1f} / "
              f"判定 {r['overall']['level']}（{r['overall']['score']}）")

    # --- 现金口径 vs 利润口径 ---
    print(f"  月度现金流：收入 {monthly['income']:,.0f} / 支出 {monthly['expense']:,.0f} "
          f"/ 结余 {monthly['balance']:,.0f}")
    if m:
        profit = m["month_profit"]
        if monthly["balance"] < profit * 0.3:
            print(f"    ❌ 现金结余远低于模型月利润（{profit:,.0f}），"
                  f"洞察会说「白忙活」，两个口径打架")
        else:
            print("    ✅ 与模型月利润量级一致，洞察结论不会自相矛盾")

    # --- 分类是否都有科目映射（否则账本品类与凭证科目都会错）---
    from categories import CATEGORY_TO_ACCOUNTS
    with db.get_conn() as conn:
        cats = [r[0] for r in conn.execute(
            "SELECT DISTINCT category FROM transactions").fetchall() if r[0]]
    unmapped = sorted(c for c in cats if c not in CATEGORY_TO_ACCOUNTS)
    print(f"  账本品类：{cats}")
    if unmapped:
        print(f"    ❌ 无科目映射（会自动落到兜底科目）：{unmapped}")
    else:
        print("    ✅ 全部品类都有对应会计科目")

    # --- 固定成本科目是否正确落在「租赁及物业费」---
    with db.get_conn() as conn:
        rent_rows = conn.execute(
            "SELECT item, category, amount FROM transactions "
            "WHERE item IN ('门店房租','水电杂费')").fetchall()
    for r in rent_rows:
        ok = r["category"] == "租赁及物业费"
        print(f"  {r['item']} {r['amount']:,.0f} 元 -> 分类 {r['category']} "
              f"{'✅' if ok else '❌ 应落在 租赁及物业费'}")

    # --- 熟客 ---
    print(f"  熟客 {len(customers)} 人，"
          f"消费记录共 {sum(c['order_count'] for c in customers)} 笔")
    if customers:
        top = customers[0]
        print(f"    光顾最多：{top['name']} {top['order_count']} 次"
              f"（常点 {top.get('favorite') or '—'}）")
    print(f"  待办提醒 {len(db.list_reminders(done=0))} 条")

    # --- 结论 ---
    print()
    if m and m["break_even_day"] and m["break_even_day"] < stats["daily_revenue"]:
        print("  ✅ 实际日销高于保本线，单店模型演示结论合理")
    elif m:
        print("  ❌ 实际日销低于保本线，演示会显示「危险」")
    if not 5 <= avg <= 12:
        print("  ❌ 客单价异常，需检查菜单/篮子分布设计")
    if len(customers) < 5:
        print("  ❌ 熟客过少，第 2 站（熟客记忆）撑不起来")


if __name__ == "__main__":
    db.init_db()
    if not _guard_repeat():
        sys.exit(1)
    print(f"生成演示数据（{MONTH_START} ~ {TODAY}，共 {DAYS} 天）：")
    _assert_categories_valid()
    _ids = seed_customers()
    seed_transactions(_ids)
    seed_reminders(_ids)
    report()
