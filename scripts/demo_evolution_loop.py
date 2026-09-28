# -*- coding: utf-8 -*-
"""演示：进化层"候选 → 验证 → 转正"完整闭环（**不调用大模型**，纯本地）。

用途：给 OPC 评审现场演示"AI 员工的经验真的会沉淀"，或自检进化护栏是否可用。

做了什么：
  1. 打印当前验证门参数（最小样本量 / 采纳数 / 任务数）
  2. 播种初始基因
  3. 造 20 条"店主采纳"的真实反馈（覆盖 2 个不同任务）
  4. 蒸馏 → 产出**候选**基因（不是直接上线）
  5. 过验证门 → 转 active；打印变更账本

用法：
    cd server
    python ..\\scripts\\demo_evolution_loop.py            # 用演示库 server/data/ai_shopkeeper.db
    python ..\\scripts\\demo_evolution_loop.py --temp     # 用临时库（不动演示数据）
    python ..\\scripts\\demo_evolution_loop.py --reset    # 先把该域的进化记录清空再跑
"""
import argparse
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER))

import config  # noqa: E402
import db  # noqa: E402
import evolution  # noqa: E402
import team_evolution  # noqa: E402
from db_evolution import get_recent_capsules  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--temp", action="store_true", help="用临时库，不动演示数据")
    ap.add_argument("--reset", action="store_true", help="先清空 copy 域的进化记录")
    ap.add_argument("--domain", default="copy")
    args = ap.parse_args()

    domain = args.domain
    if args.temp:
        import tempfile
        db.DB_PATH = Path(tempfile.mkdtemp()) / "evolution-demo.db"
        db._schema_ready.clear()

    db.init_db()
    min_samples = config.EVOLUTION_MIN_SAMPLES
    print(f"库：{db.DB_PATH}")
    print(f"验证门：最小样本 {min_samples}，采纳 ≥ {config.EVOLUTION_VERIFY_MIN_ADOPTED}，"
          f"任务 ≥ {config.EVOLUTION_VERIFY_MIN_TASKS}"
          f"（基准命令：{config.EVOLUTION_VERIFY_CMD or '未配置'}）")

    if args.reset:
        from db import get_conn
        with get_conn() as conn:
            for t in ("agent_capsules", "agent_events", "agent_learnings", "agent_genes"):
                conn.execute(f"DELETE FROM {t} WHERE domain=? OR domain IS NULL", (domain,))
        print("已清空该域进化记录")

    seeded = team_evolution.seed_initial_genes()
    print(f"① 初始基因：{seeded} 条")

    gene_id = None
    for g in db.get_active_genes(domain):
        gene_id = g["gene_id"]
        break
    if not gene_id:
        print("✗ 没有可用基因，退出")
        return 1

    # ② 造真实反馈：20 条采纳，覆盖 2 个不同任务
    n = max(min_samples, 20)
    for i in range(n):
        evolution.record_outcome(
            domain, gene_id, f"第{i+1}条店主采纳的文案",
            user_adopted=True, user_edited=False,
            task_context={"task": f"开业文案-{i % 2}"})   # 2 个不同任务
    print(f"② 记录真实反馈：{n} 条采纳（覆盖 2 个任务）")

    # ③ 蒸馏 → 候选（不直接上线）
    candidate = evolution.distill_skill(domain)
    if not candidate:
        print("✗ 蒸馏未产出候选（可能未达阈值或 24h 内已蒸馏过）")
        return 1
    print(f"③ 蒸馏产出候选：{candidate['gene_id']}（status={candidate['status']}）")

    # ④ 过验证门 → 转正
    result = evolution.verify_candidate(candidate["gene_id"])
    print(f"④ 验证门：{'✅ 通过' if result['ok'] else '❌ 未通过'} "
          f"（证据 adopted={result['evidence']['adopted']} / tasks={result['evidence']['distinct_tasks']}）")
    gene = db.get_gene(candidate["gene_id"])
    print(f"   转正后状态：{gene['status']}")

    # ⑤ 变更账本（审计）
    print("⑤ 变更账本（最近 8 条）：")
    for e in evolution.gene_ledger(domain, limit=8)[::-1]:
        print(f"   · {e.get('created_at','')} [{e.get('event_type')}] "
              f"{e.get('gene_id') or '-'} {str(e.get('details') or '')[:60]}")
    return 0 if gene["status"] == "active" else 1


if __name__ == "__main__":
    raise SystemExit(main())
