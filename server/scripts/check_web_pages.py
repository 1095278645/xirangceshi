# -*- coding: utf-8 -*-
"""check_web_pages.py — 网页版（server/static）静态自检

## 为什么需要

网页版把逻辑写在**内联 onclick**里（`onclick="foo(1)"`），而 foo 是否存在
只有点击那一刻才知道 —— 写错了在浏览器控制台报一条 ReferenceError，
功能等于不可用，但页面看起来完全正常。这和小程序的 `bindtap` 是同一类坑
（那边由 check_mp_pages.py 守着），网页端此前完全没有检查。

本脚本检查：
  1. 每个页面 js 里 `onclick="fn(...)"` / `onchange=` 等内联事件引用的函数，
     必须在所有 js 文件中定义（含 core.js 与其它页面）
  2. core.js 的 render() 里 `r === 'xxx'` 的每个路由，必须有对应的
     `renderXxx` 定义；go() 里的每个路由必须有 `loadXxx`（或明确不需要加载）
  3. index.html 抽屉里的 data-route 必须在 render 分发里存在（否则点了没反应）
  4. index.html 引用的 js 文件必须都存在
  5. 所有文本文件 UTF-8 可解码且无乱码特征

用法：cd server && python scripts/check_web_pages.py
退出码 0=通过，1=有问题。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
STATIC = SERVER / "static"
MOJIBAKE = ("锛", "銆", "鐨", "绉", "涓枃", "娴", "鍟", "鏃", "鐢", "鍙")

# 内联事件里可能用到的浏览器/全局函数，不属于本项目的业务函数。
# 注意不要把 window/document 放进来：它们出现在 `window.open(...)` 这类成员调用里，
# 我们的正则会把 `open` 当成被调函数，从而误报"未定义"。
BUILTINS = {
    "alert", "confirm", "prompt", "console", "location",
    "parseFloat", "parseInt", "Number", "String", "Boolean", "Math", "JSON",
    "Object", "Array", "Date", "isNaN", "encodeURIComponent", "setTimeout",
    "clearTimeout", "fetch", "FormData", "this", "event", "return", "if",
    "open", "close", "print", "scrollTo", "focus", "blur", "reload",
}
# 事件属性里除了函数调用，还可能直接写表达式语句
EVENT_ATTR = re.compile(
    r'\bon(?:click|change|input|submit|blur|focus)\s*=\s*"([^"]*)"', re.I)
IDENT_CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
# 成员调用 window.open( / state.x() 里的方法名不算顶层函数
MEMBER_CALL = re.compile(r"\.\s*([A-Za-z_$][\w$]*)\s*\(")


def collect_js() -> dict[str, str]:
    # 键统一用 POSIX 风格（'js/core.js'）：Windows 上 relative_to 会给出
    # 'js\\core.js'，按字面量取就会拿到空串（第一版就因此把 core.js 读成空文件，
    # 于是所有路由都被误报成"没有对应页面"）。
    return {p.relative_to(STATIC).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(STATIC.rglob("*.js"))}


def defined_functions(sources: dict[str, str]) -> set[str]:
    names: set[str] = set()
    for text in sources.values():
        names |= set(re.findall(
            r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", text, re.M))
        names |= set(re.findall(
            r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
            r"(?:function|\()", text, re.M))
    return names


def collect_inline_calls(sources: dict[str, str]) -> dict[str, set[str]]:
    """从 js 里的内联事件属性抽函数名（只扫字符串里的 onclick=...）。"""
    found: dict[str, set[str]] = {}
    for rel, text in sources.items():
        calls: set[str] = set()
        for expr in EVENT_ATTR.findall(text):
            members = set(MEMBER_CALL.findall(expr))
            for name in IDENT_CALL.findall(expr):
                if name in members:
                    continue          # 成员方法（state.x() / window.open()）
                calls.add(name)
        if calls:
            found[rel] = calls
    return found


def main() -> int:
    problems: list[str] = []
    warnings: list[str] = []

    if not STATIC.exists():
        print("找不到 server/static 目录")
        return 1

    sources = collect_js()
    defined = defined_functions(sources)
    print(f"扫描 {len(sources)} 个 js 文件，共 {len(defined)} 个顶层函数")

    # 1) 内联事件引用
    for rel, calls in sorted(collect_inline_calls(sources).items()):
        missing = sorted(c for c in calls if c not in defined and c not in BUILTINS)
        if missing:
            problems.append(f"{rel}: 内联事件引用了未定义的函数 {missing}")

    # 2) render / go 路由闭环
    core = sources.get("js/core.js", "")
    render_routes = set(re.findall(r"r\s*===\s*'([^']+)'", core))
    go_routes = set(re.findall(r"route\s*===\s*'([^']+)'", core))
    # home 走 else 分支（默认页），没有显式 `r === 'home'`，属于正常
    render_routes.add("home")
    for route in sorted(render_routes - {"more"}):
        fn = "render" + route[0].upper() + route[1:]
        if fn not in defined:
            problems.append(f"core.js 路由 '{route}' 需要 {fn}()，但没有定义")
    for route in sorted(go_routes - {"more"}):
        if route in ("home", "custDetail"):
            continue        # home 是默认页；custDetail 由熟客页内部跳转
        fn = "load" + route[0].upper() + route[1:]
        if fn not in defined:
            warnings.append(f"core.js 路由 '{route}' 没有 {fn}()（页面不会自动加载数据）")

    # 3) index.html 抽屉项 ↔ 路由
    idx = (STATIC / "index.html").read_text(encoding="utf-8")
    for route in sorted(set(re.findall(r'data-route="([^"]+)"', idx))):
        if route == "more":
            continue
        if route not in render_routes:
            problems.append(f"index.html 的 data-route='{route}' 在 core.js 里没有对应页面")

    # 4) index.html 引用的 js 是否都存在
    for src in re.findall(r'<script src="/static/([^"?]+)', idx):
        if not (STATIC / src).exists():
            problems.append(f"index.html 引用了不存在的脚本 /static/{src}")

    # 5) 编码自检
    for p in sorted(STATIC.rglob("*")):
        if not p.is_file() or p.suffix not in (".js", ".html", ".css"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            problems.append(f"{p.relative_to(STATIC)} 非法 UTF-8：{e}")
            continue
        hit = [m for m in MOJIBAKE if m in text]
        if hit:
            problems.append(f"{p.relative_to(STATIC)} 含乱码特征 {hit}")

    if problems:
        print("\n问题（必须修）：")
        for p in problems:
            print("  ✗", p)
    else:
        print("✅ 网页端内联事件、路由闭环、脚本引用、文件编码均正常")
    if warnings:
        print(f"\n提示（{len(warnings)}）：")
        for w in warnings:
            print("  ⚠", w)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
