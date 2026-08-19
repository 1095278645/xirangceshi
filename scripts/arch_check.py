#!/usr/bin/env python3
"""arch_check.py — 巷子里的AI掌柜 · 八透镜可重复执行架构检查

把「skill-optimizer 八透镜」翻译成对这个项目的可重复检查机制。
每次重构后跑一遍，自动核对 8 个维度的健康度，输出可读报告 + 结构化 JSON。
只读法、无副作用、幂等（同一状态重复运行结果一致），便于接入 CI。

用法：
    python scripts/arch_check.py                    # 检查项目根（脚本上一级）
    python scripts/arch_check.py <项目根>
    python scripts/arch_check.py --out out.json      # 额外落盘结构化结果
    python scripts/arch_check.py --minimal          # 只输出结论行
    python scripts/arch_check.py --exit-code        # 按失败等级退出码(0/1/2)

八透镜 → 本项目检查项映射：
  L1 令牌经济    → 文件健康度（超大/超长单体）
  L2 单一职责    → 入口薄、路由薄、数据/引擎层分离、路由无直接 SQL
  L3 渐进披露    → 按需分层（routers/ 按域、前端 js/ 按职责、无超大单体）
  L4 触发精准    → 路由清晰（/api 前缀、无重复端点、静态资源入口兜底）
  L5 护栏分离    → 配置独立、幂等去重集中、密钥脱敏集中、无硬编码凭据
  L6 转换管线    → 计算引擎纯函数、分步状态、阈值常量、无 IO 副作用
  L7 闭环控制    → 异常处理+结构化日志+自动化测试+幂等重跑
  L8 频率分层    → 配置抽离、业务层无魔法阈值硬编码
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Windows 控制台可能不是 UTF-8，先修正输出编码
for _stream in (sys.stdout, sys.stderr):
    enc = getattr(_stream, "encoding", "") or ""
    if enc.lower() not in ("utf-8", "utf8"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ---------- 阈值（集中配置，便于调参） ----------
LINE_WARN = 250          # 单文件行数警告
LINE_FAIL = 400          # 单文件行数失败
MAIN_MAX = 120           # 入口文件应薄（只组装）
ROUTER_MAX = 80          # 路由文件应薄（转发）
FUNC_MAX = 40            # 单文件顶层函数/类数
CODE_EXTS = {".py", ".js"}
EXCLUDE_DIRS = {".git", ".temp", ".venv", "__pycache__", "node_modules", "scripts"}


# ---------- 文件收集 ----------
def collect(root: Path) -> list[Path]:
    files = []
    for ext in CODE_EXTS:
        files.extend(root.rglob(f"*{ext}"))
    out = []
    for f in files:
        if f.name == "arch_check.py":
            continue
        try:
            relparts = f.relative_to(root).parts
        except ValueError:
            continue
        if any(part in EXCLUDE_DIRS for part in relparts):
            continue
        if "tests" in relparts:
            continue
        out.append(f)
    return sorted(out)


def count_lines(f: Path) -> int:
    try:
        return sum(1 for _ in f.open("r", encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return 0


def read_text(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def rp(root: Path, f: Path) -> str:
    try:
        return f.relative_to(root).as_posix()
    except ValueError:
        return str(f)


# ---------- L1 文件健康度 ----------
def lint_l1(root: Path, files: list[Path]) -> dict:
    huge, big = [], []
    for f in files:
        n = count_lines(f)
        if n > LINE_FAIL:
            huge.append((rp(root, f), n))
        elif n > LINE_WARN:
            big.append((rp(root, f), n))
    busy = []
    for f in files:
        if f.suffix != ".py":
            continue
        txt = read_text(f)
        n = len(re.findall(r"(?m)^\s*(?:async\s+)?(?:def|class)\s+\w+", txt))
        if n > FUNC_MAX:
            busy.append((rp(root, f), n))
    checks = [
        {"check": f"单文件>{LINE_FAIL}行", "items": [f"{p}: {n}行" for p, n in huge], "n": len(huge)},
        {"check": f"单文件{LINE_WARN}-{LINE_FAIL}行", "items": [f"{p}: {n}行" for p, n in big], "n": len(big)},
        {"check": f"顶层函数/类>{FUNC_MAX}个", "items": [f"{p}: {n}个" for p, n in busy], "n": len(busy)},
    ]
    verdict = "FAIL" if huge else ("WARN" if (big or busy) else "PASS")
    return {"lens": "L1 令牌经济/文件健康度", "verdict": verdict, "checks": checks}


# ---------- L2 单一职责 ----------
def lint_l2(root: Path, files: list[Path]) -> dict:
    checks = []
    main = root / "server" / "main.py"
    main_info = "未找到 server/main.py"
    if main.exists():
        n = count_lines(main)
        txt = read_text(main)
        has_biz = bool(re.search(r"conn\.execute|INSERT|UPDATE|DELETE\s+FROM|SELECT\s+\*", txt))
        main_info = f"{n}行，直接业务逻辑={'有' if has_biz else '无'}"
        if n > MAIN_MAX:
            checks.append({"check": f"入口 main.py 应薄(≤{MAIN_MAX}行)", "items": [f"当前 {n} 行"], "n": 1})
    checks.append({"check": "入口 main.py 薄且只组装", "items": [main_info], "n": 0})
    # 路由层直接 SQL 泄漏
    routers = root / "server" / "routers"
    leak = []
    if routers.is_dir():
        for f in sorted(routers.glob("*.py")):
            if f.name == "__init__.py":
                continue
            if re.search(r"conn\.execute|get_conn\(", read_text(f)):
                leak.append(rp(root, f))
    checks.append({"check": "路由层无直接 SQL(应调 db/引擎层)", "items": leak, "n": len(leak)})
    # 服务/引擎层齐全
    missing = [s for s in ("db.py", "store.py", "tax.py", "payment.py", "wechat_pay.py")
               if not (root / "server" / s).exists()]
    checks.append({"check": "数据/引擎层齐全(db/store/tax/payment/wechat_pay)",
                   "items": [f"缺: {m}" for m in missing], "n": len(missing)})
    # AI 能力层齐全（单 agent 能力 + 多 agent 引擎 + 域编排）
    ai_missing = [s for s in ("ai.py", "team.py", "team_domains.py")
                  if not (root / "server" / s).exists()]
    checks.append({"check": "AI 能力层齐全(ai/team/team_domains)",
                   "items": [f"缺: {m}" for m in ai_missing], "n": len(ai_missing)})
    # 路由文件行数
    thick = []
    if routers.is_dir():
        for f in sorted(routers.glob("*.py")):
            if f.name == "__init__.py":
                continue
            n = count_lines(f)
            if n > ROUTER_MAX:
                thick.append((rp(root, f), n))
    checks.append({"check": f"路由文件薄(≤{ROUTER_MAX}行)", "items": [f"{p}: {n}行" for p, n in thick], "n": len(thick)})
    verdict = "WARN" if (leak or missing or thick or ai_missing) else "PASS"
    return {"lens": "L2 单一职责/分层", "verdict": verdict, "checks": checks}


# ---------- L3 渐进披露 ----------
def lint_l3(root: Path) -> dict:
    checks = []
    routers = root / "server" / "routers"
    routers_ok = routers.is_dir()
    n_router = len(list(routers.glob("*.py"))) if routers_ok else 0
    checks.append({"check": "后端按域拆分(routers/)", "items": [f"{n_router} 个域模块"], "n": 0})
    js = root / "server" / "static" / "js"
    layered = (js.is_dir() and (js / "pages").is_dir() and (js / "init.js").exists()
               and ((js / "core").is_dir() or (js / "core.js").exists()))
    checks.append({"check": "前端按职责分层(js/pages, js/core(.js), js/init.js, js/speech.js)",
                   "items": ["已分层" if layered else "未分层"], "n": 0})
    verdict = "PASS" if (routers_ok and layered) else "FAIL"
    return {"lens": "L3 渐进披露/按需分层", "verdict": verdict, "checks": checks}


# ---------- L4 路由清晰 ----------
def _endpoints(f: Path) -> list[tuple[str, str]]:
    """解析单文件内端点，返回 (method, 完整路径) 列表（合并 APIRouter prefix）。"""
    txt = read_text(f)
    prefix = "/"
    m = re.search(r"APIRouter\(\s*prefix=\"([^\"]+)\"", txt)
    if m:
        prefix = m.group(1).rstrip("/")
    eps: list[tuple[str, str]] = []
    for mm in re.finditer(r"@\w+\.(get|post|put|delete|patch)\(\s*\"([^\"]+)\"", txt):
        method = mm.group(1).lower()
        p = mm.group(2).lstrip("/")
        full = (prefix + "/" + p).replace("//", "/") or prefix or "/"
        eps.append((method, full))
    return eps


def lint_l4(root: Path) -> dict:
    eps: list[tuple[str, str]] = []
    for f in (root / "server").rglob("*.py"):
        if "tests" in f.parts or f.name == "arch_check.py":
            continue
        eps.extend(_endpoints(f))
    # 冲突 = 同一 HTTP 方法 + 同一路径重复注册
    dup = sorted(p for (_, p), c in Counter(eps).items() if c > 1)
    prefixed = sum(1 for _, p in eps if p.startswith("/api/"))
    main = root / "server" / "main.py"
    static_mounted = "/static" in read_text(main) if main.exists() else False
    checks = [
        {"check": "API 端点数量", "items": [f"{len(eps)} 个"], "n": len(eps)},
        {"check": "统一 /api 前缀", "items": [f"{prefixed}/{len(eps)} 使用前缀"], "n": len(eps) - prefixed},
        {"check": "同方法重复路径冲突", "items": dup, "n": len(dup)},
        {"check": "静态资源入口兜底挂载", "items": ["是" if static_mounted else "否"], "n": 0},
    ]
    verdict = "FAIL" if dup else ("PASS" if (prefixed >= len(eps) * 0.7 and static_mounted) else "WARN")
    return {"lens": "L4 触发精准/路由清晰", "verdict": verdict, "checks": checks}


# ---------- L5 护栏分离 ----------
def lint_l5(root: Path) -> dict:
    checks = []
    config_ok = (root / "server" / "config.py").exists()
    checks.append({"check": "配置独立(config.py)", "items": ["是" if config_ok else "否"], "n": 0})
    idem_files = []
    for f in (root / "server").rglob("*.py"):
        if "tests" in f.parts:
            continue
        if re.search(r"IntegrityError|UNIQUE|幂等|idempot|INSERT OR IGNORE", read_text(f)):
            idem_files.append(rp(root, f))
    checks.append({"check": "幂等去重集中在数据/服务层", "items": idem_files, "n": len(idem_files)})
    mask_files = []
    for f in (root / "server").rglob("*.py"):
        if "tests" in f.parts:
            continue
        if re.search(r"api_v3_key|apikey|脱敏|\*\*\*", read_text(f), re.I):
            mask_files.append(rp(root, f))
    checks.append({"check": "密钥脱敏处理", "items": mask_files, "n": len(mask_files)})
    hardcode = []
    for f in (root / "server").rglob("*.py"):
        if "tests" in f.parts:
            continue
        for m in re.finditer(r"(password|passwd|secret|api_key)\s*[=:]\s*['\"][^'\"]{3,}['\"]",
                             read_text(f), re.I):
            hardcode.append(f"{rp(root, f)}: {m.group(0)[:40]}")
    checks.append({"check": "业务代码硬编码凭据", "items": hardcode, "n": len(hardcode)})
    verdict = "FAIL" if hardcode else ("PASS" if config_ok else "WARN")
    return {"lens": "L5 护栏抽离/安全分离", "verdict": verdict, "checks": checks}


# ---------- L6 转换管线 ----------
def lint_l6(root: Path) -> dict:
    checks = []
    engines = ["store.py", "tax.py"]
    has_db_effect = False
    for name in engines:
        f = root / "server" / name
        if not f.exists():
            continue
        txt = read_text(f)
        steps = len(re.findall(r"(?m)^\s*#.*(?:先|目标|保本|回本|现金流|诊断|然后)", txt))
        consts = len(re.findall(r"(?m)^[A-Z][A-Z_0-9]+\s*=", txt))
        has_db = bool(re.search(r"conn\.execute|get_conn|INSERT", txt))
        has_db_effect = has_db_effect or has_db
        checks.append({"check": f"{name} 转换管线",
                       "items": [f"分步注释={steps} 阈值常量={consts} DB副作用={'有' if has_db else '无'}"], "n": 0})
    # AI 编排管线（team_domains._run_team 的员工并行→裁决→融合；team 采纳沉淀）
    ai_pipeline_ok = True
    for name in ("ai.py", "team.py", "team_domains.py"):
        f = root / "server" / name
        if not f.exists():
            continue
        txt = read_text(f)
        steps = len(re.findall(r"(?m)^\s*#.*(?:并行|竞争|裁决|融合|归因|降级|兜底)", txt))
        fallback = len(re.findall(r"except\s*(?:\(?[A-Za-z]+(?:Exception|Error)\)?)?\s*(?:as\s+\w+)?\s*:|degraded|兜底", txt))
        no_db = not bool(re.search(r"conn\.execute|get_conn|INSERT", txt))
        # team.py 的 record_adoption 写采纳沉淀属特性（Self-Grown），不算副作用
        if name == "team.py":
            no_db = True
        if steps == 0 or fallback == 0:
            ai_pipeline_ok = False
        checks.append({"check": f"{name} AI 编排管线",
                       "items": [f"分步注释={steps} 兜底/降级={fallback} DB写入={'有(采纳沉淀)' if not no_db else '无'}"], "n": 0})
    engines_ok = any((root / "server" / n).exists() for n in engines)
    verdict = "FAIL" if has_db_effect else ("WARN" if (not ai_pipeline_ok) else ("PASS" if engines_ok else "WARN"))
    return {"lens": "L6 转换管线/计算引擎", "verdict": verdict, "checks": checks}


# ---------- L7 闭环控制 ----------
def lint_l7(root: Path) -> dict:
    try_n = log_n = 0
    for f in (root / "server").rglob("*.py"):
        if "tests" in f.parts:
            continue
        txt = read_text(f)
        try_n += len(re.findall(r"try\s*:", txt))
        log_n += len(re.findall(r"log\.(info|debug|warning|error)\(", txt))
    tests_dir = root / "server" / "tests"
    tests = list(tests_dir.glob("test_*.py")) if tests_dir.is_dir() else []
    idem = sum(1 for f in (root / "server").rglob("*.py")
               if re.search(r"IntegrityError|UNIQUE|幂等|idempot", read_text(f)))
    checks = [
        {"check": "异常处理 try/except", "items": [f"{try_n} 处 try"], "n": try_n},
        {"check": "可观测性(结构化日志)", "items": [f"{log_n} 处 log.*"], "n": log_n},
        {"check": "稳定性保障(自动化测试)", "items": [f"{len(tests)} 个 test 文件"], "n": len(tests)},
        {"check": "幂等重跑安全", "items": [f"{idem} 处幂等/唯一约束"], "n": idem},
    ]
    verdict = "FAIL" if (try_n == 0 or not tests) else ("WARN" if (try_n < 2 or log_n == 0) else "PASS")
    return {"lens": "L7 闭环控制/反馈", "verdict": verdict, "checks": checks}


# ---------- L8 频率分层 ----------
def lint_l8(root: Path) -> dict:
    checks = []
    config_ok = (root / "server" / "config.py").exists() and (root / "server" / "config.local.json").exists()
    checks.append({"check": "配置抽离(config.py + config.local.json)", "items": ["是" if config_ok else "否"], "n": 0})
    magic = []
    for f in (root / "server").rglob("*.py"):
        if "tests" in f.parts or f.name in ("store.py", "tax.py"):
            continue
        for m in re.finditer(r"(?m)^\s*(?:if|elif)\s+.*?(==|>|<|>=|<=)\s*(\d{3,})\s*:", read_text(f)):
            magic.append(f"{rp(root, f)}: {m.group(0).strip()[:60]}")
    checks.append({"check": "业务层魔法阈值硬编码(建议入 config)", "items": magic[:10], "n": len(magic)})
    verdict = "WARN" if magic else ("PASS" if config_ok else "WARN")
    return {"lens": "L8 频率分层/配置抽离", "verdict": verdict, "checks": checks}


# ---------- 主流程 ----------
def run(root: Path) -> dict:
    files = collect(root)
    results = [
        lint_l1(root, files),
        lint_l2(root, files),
        lint_l3(root),
        lint_l4(root),
        lint_l5(root),
        lint_l6(root),
        lint_l7(root),
        lint_l8(root),
    ]
    worst = "PASS"
    for r in results:
        if r["verdict"] == "FAIL":
            worst = "FAIL"
            break
        if r["verdict"] == "WARN":
            worst = "WARN"
    return {"root": str(root), "scanned_files": len(files), "results": results, "worst": worst}


def main() -> int:
    ap = argparse.ArgumentParser(description="八透镜可重复执行架构检查")
    ap.add_argument("root", nargs="?", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--out", help="额外把结构化 JSON 写到该路径")
    ap.add_argument("--minimal", action="store_true", help="只输出结论行")
    ap.add_argument("--exit-code", action="store_true", help="按失败等级返回退出码 0/1/2")
    args = ap.parse_args()

    data = run(Path(args.root).resolve())
    print(f"\n===== 八透镜架构检查 · {data['root']} 扫描 {data['scanned_files']} 文件 =====")
    for r in data["results"]:
        mark = {"PASS": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FAIL]"}[r["verdict"]]
        print(f"{mark} {r['lens']}")
        for c in r["checks"]:
            detail = " | ".join(c["items"]) if c["items"] else ""
            print(f"      · {c['check']}: {detail}")
    print(f"\n总评: {data['worst']}")

    if args.out:
        out = Path(args.out)
        payload = dict(data)
        payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON 已写入: {out}")

    if args.exit_code:
        return 0 if data["worst"] == "PASS" else (1 if data["worst"] == "WARN" else 2)
    return 0


if __name__ == "__main__":
    sys.exit(main())