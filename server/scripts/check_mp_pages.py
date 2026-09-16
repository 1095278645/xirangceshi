"""check_mp_pages.py — 小程序页面静态自检（WXML 绑定 ↔ JS 方法、页面注册、编码）

## 为什么需要

小程序编译期**不检查** `bindtap="foo"` 里的 foo 是否存在：写错了在真机上点了没反应，
演示时最难排查（没有报错、没有日志）。同理 app.json 里漏注册页面、
页面 json 与 wxml 不配套也都不报错。

本脚本检查：
  1. 每个 wxml 里的 bind*/catch* 处理函数，必须在其同名 .js 里定义
  2. app.json pages 里的每个页面，四件套（js/json/wxml/wxss）是否齐全
  3. 页面目录存在但未注册在 app.json（新页面容易忘记注册）
  4. 所有文本文件的 UTF-8 可解码 + 无常见乱码特征
     （历史上用 PowerShell 写入把中文写坏过三次）

用法：cd server && python scripts/check_mp_pages.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MP = Path(__file__).resolve().parent.parent.parent / "miniprogram"

# 常见的"中文被按错误编码重写"后的特征字符
MOJIBAKE = ("锛", "銆", "鐨", "绉", "涓枃", "娴", "鍟", "鏃", "鐢", "鍙")

BIND_RE = re.compile(r'\b(?:bind|catch)([a-zA-Z]+)\s*=\s*"([^"{}]+)"')


def js_methods(js_text: str) -> set[str]:
    """粗略取出 JS 里定义的方法名（Page({...}) 与 Component({methods:{...}})）。

    组件的方法写在 `methods: {` 里，缩进比 Page 深一层，所以这里不限定缩进宽度
    ——第一版写死 `^\\s{2}` 结果把 tabbar 的 go() 漏掉了，误报成"方法不存在"。
    """
    # 方法简写：`go(e) {` / `onLoad() {`（不限定缩进，组件里缩进更深）。
    # 参数部分必须用 [^){]* 而不是 [^)]*：后者会跨行贪婪匹配，
    # 把 `Component({\n ... methods:{\n go(e) {` 整段吃成一个匹配（第一版就踩了，
    # 结果 go() 没被识别，反而误报"方法不存在"）。
    names = set(re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*\([^){]*\)\s*\{",
                           js_text, re.M))
    # 属性式定义：`go: function (e) {` / `go: (e) => {`
    names |= set(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*(?:function)?\s*\(",
                            js_text))
    names |= set(re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?\(",
                            js_text, re.M))
    # 控制流关键字会命中上面的正则，但不是事件处理器，排除掉避免误报
    return names - {"if", "for", "while", "switch", "catch", "return",
                    "function", "Component", "Page", "Behavior"}


def main() -> int:
    problems: list[str] = []
    warnings: list[str] = []

    app_json = json.loads((MP / "app.json").read_text(encoding="utf-8"))
    pages = app_json.get("pages", [])

    # 1/2/3：页面四件套与注册
    for page in pages:
        base = MP / page
        for ext in (".js", ".json", ".wxml", ".wxss"):
            if not (base.parent / (base.name + ext)).exists():
                warnings.append(f"{page}{ext} 不存在（小程序仍可运行，但通常应补齐）")

    for d in sorted(p for p in MP.glob("pages/*") if p.is_dir()):
        rel = f"pages/{d.name}/{d.name}"
        if rel not in pages and not any(p.startswith(f"pages/{d.name}/") for p in pages):
            problems.append(f"{rel} 存在但未注册在 app.json 的 pages 里（用户进不去）")

    # 4：WXML 绑定 ↔ JS 方法
    for wxml in sorted(MP.glob("**/*.wxml")):
        js = wxml.with_suffix(".js")
        if not js.exists():
            continue
        js_text = js.read_text(encoding="utf-8")
        methods = js_methods(js_text)
        for kind, handler in BIND_RE.findall(wxml.read_text(encoding="utf-8")):
            if handler not in methods and handler not in ("", "true", "false"):
                problems.append(
                    f"{wxml.relative_to(MP)} 绑定 {kind}=\"{handler}\"，"
                    f"但 {js.name} 里没有这个方法")

    # 5：编码自检
    for f in sorted(MP.glob("**/*")):
        if not f.is_file() or f.suffix not in (".js", ".json", ".wxml", ".wxss"):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            problems.append(f"{f.relative_to(MP)} 不是合法 UTF-8：{e}")
            continue
        hit = [m for m in MOJIBAKE if m in text]
        if hit:
            problems.append(f"{f.relative_to(MP)} 含乱码特征 {hit}（曾因非 UTF-8 写入损坏）")

    print(f"检查 {len(pages)} 个已注册页面、{len(list(MP.glob('**/*.wxml')))} 个 wxml")
    if problems:
        print("\n问题（必须修）：")
        for p in problems:
            print("  ✗", p)
    else:
        print("✅ 页面注册、事件绑定、文件编码均正常")
    if warnings:
        print(f"\n提示（{len(warnings)}）：")
        for w in warnings:
            print("  ⚠", w)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
