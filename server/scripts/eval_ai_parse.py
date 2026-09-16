# -*- coding: utf-8 -*-
"""eval_ai_parse.py — 用真实模型评测"一句话记账"的准确率

## 为什么做这个

产品最核心的承诺是「说一句话就记好账」。但到目前为止，我们所有测试都是**打桩**的
（mock 掉 ai.parse_transaction 返回固定结果），也就是说：**这个承诺从没被验证过**。
后端接口、账本、报表都验得很扎实，唯独最核心的那一步是空白。

本脚本拿一批**真实店主会说的话**去调真实模型，逐条核对：
  金额 / 收支方向 / 分类 / 熟客 / 事由
并把模型返回的原始 JSON 一起打出来，便于判断错在哪、是提示词问题还是模型能力问题。

用法：
  cd server && python scripts/eval_ai_parse.py            # 全部案例
  cd server && python scripts/eval_ai_parse.py --limit 5  # 先跑前 5 条（省额度）

注意：会真实消耗 API 额度（约 20 次调用）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# (店主原话, 期望金额, 期望方向, 期望分类关键字, 期望熟客)
CASES = [
    # ---- 最典型的早点摊说法 ----
    ("王阿姨买了两个肉包一杯豆浆，6块", 6, "income", "主营业务收入", "王阿姨"),
    ("李叔拿了两斤猪肉，38，支出", 38, "expense", "进货", "李叔"),
    ("刚卖了30块钱的包子", 30, "income", "主营业务收入", ""),
    ("收了张叔20块", 20, "income", "主营业务收入", "张叔"),
    ("买面粉花了120", 120, "expense", "进货", ""),
    ("今天房租交了6000", 6000, "expense", "租赁及物业费", ""),
    ("水电费这个月580", 580, "expense", "租赁及物业费", ""),
    ("进了500块钱的货", 500, "expense", "进货", ""),
    # ---- 口语/不规范说法（真实输入的主要形态）----
    ("早上卖了八百多", 800, "income", "主营业务收入", ""),
    ("刘姐买了三杯豆浆，一共九块", 9, "income", "主营业务收入", "刘姐"),
    ("老郑早餐吃了12块5", 12.5, "income", "主营业务收入", "老郑"),
    ("给小工发了3500工资", 3500, "expense", "职工薪酬", ""),
    ("买了点办公用品，45块", 45, "expense", "办公费", ""),
    ("王阿姨充了200块会员卡", 200, "income", "主营业务收入", "王阿姨"),
    ("打广告花了800", 800, "expense", "广告宣传费", ""),
    # ---- 容易搞错方向的 ----
    ("退了顾客15块", 15, "expense", "", ""),
    ("张叔欠着60块，先记上", 60, "income", "", "张叔"),
    ("今天收入1250，支出320", None, "", "", ""),     # 一句两笔：看它怎么处理
    # ---- 边界：金额没说清 ----
    ("张叔拿了个包子", None, "income", "", "张叔"),
    ("修了下冰箱", None, "", "", ""),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show-raw", action="store_true")
    args = ap.parse_args()

    import ai
    if not ai.ai_available():
        print("未配置 API Key，无法评测（这本身也说明演示环境要配 Key）")
        return 1

    cases = CASES[:args.limit] if args.limit else CASES
    print(f"用真实模型评测 {len(cases)} 条店主原话")
    print(f"模型：{ai.load_settings().get('model')}  base：{ai.load_settings().get('base_url')}")
    print("=" * 96)

    rows = []
    hits = {"amount": 0, "direction": 0, "category": 0, "customer": 0}
    total = 0
    for text, exp_amt, exp_dir, exp_cat, exp_cust in cases:
        t0 = time.time()
        try:
            got = ai.parse_transaction(text)
        except Exception as e:  # noqa: BLE001
            print(f"❌ 调用失败：{text}\n   {type(e).__name__}: {e}")
            rows.append({"text": text, "error": str(e)})
            continue
        dt = time.time() - t0

        g_amt = got.get("amount")
        g_dir = got.get("trans_type")
        g_cat = got.get("category") or ""
        g_cust = got.get("customer") or ""

        ok_amt = (exp_amt is None and g_amt is None) or \
                 (exp_amt is not None and g_amt is not None and abs(float(g_amt) - exp_amt) < 0.01)
        ok_dir = (not exp_dir) or g_dir == exp_dir
        ok_cat = (not exp_cat) or (exp_cat in g_cat)
        ok_cust = (not exp_cust) or (exp_cust == g_cust)

        total += 1
        hits["amount"] += bool(ok_amt)
        hits["direction"] += bool(ok_dir)
        hits["category"] += bool(ok_cat)
        hits["customer"] += bool(ok_cust)
        all_ok = ok_amt and ok_dir and ok_cat and ok_cust

        mark = "✅" if all_ok else "⚠️ "
        flags = "".join([
            "" if ok_amt else "金额 ",
            "" if ok_dir else "方向 ",
            "" if ok_cat else "分类 ",
            "" if ok_cust else "熟客 ",
        ])
        print(f"{mark} {text}")
        print(f"    期望：金额={exp_amt} 方向={exp_dir or '-'} 分类={exp_cat or '-'} 熟客={exp_cust or '-'}")
        print(f"    实得：金额={g_amt} 方向={g_dir} 分类={g_cat} 熟客={g_cust or '-'}   ({dt:.1f}s)"
              + (f"   ← 偏：{flags}" if flags else ""))
        if args.show_raw:
            print("    raw: " + json.dumps(got, ensure_ascii=False))
        rows.append({"text": text, "got": got, "ok": all_ok,
                     "expected": {"amount": exp_amt, "dir": exp_dir,
                                  "cat": exp_cat, "cust": exp_cust}})

    print("=" * 96)
    if total:
        print(f"逐项准确率（{total} 条）：")
        print(f"  金额识别   {hits['amount']:>2}/{total}  {hits['amount']/total*100:.0f}%")
        print(f"  收支方向   {hits['direction']:>2}/{total}  {hits['direction']/total*100:.0f}%")
        print(f"  分类       {hits['category']:>2}/{total}  {hits['category']/total*100:.0f}%")
        print(f"  熟客识别   {hits['customer']:>2}/{total}  {hits['customer']/total*100:.0f}%")
        whole = sum(1 for r in rows if r.get("ok"))
        print(f"  四项全对   {whole:>2}/{total}  {whole/total*100:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
