"""check_mp_api.py — 小程序 ↔ 后端接口一致性静态检查

## 为什么需要

小程序里写错一个接口路径，编译不报错、真机演示时才 404；而演示现场最怕这个。
本项目已经因此踩过两次：
  - 收款页 JS 里 fetch 了不存在的 /api/pay/<token>（少了 /info）
  - 小程序 utils/api.js 里写 /api/backup/export 用 GET，后端实际是 POST

本脚本做两件事：
  1. 把 utils/api.js 里所有 request('...') 的路径抽出来，与实际注册的
     FastAPI 路由表比对（存在性 + 方法是否匹配）。
  2. 反向抽查：后端已注册、但小程序完全没有引用的接口清单（不是错误，
     只是提示，便于发现"后端做了功能但前端没入口"）。

用法：cd server && python scripts/check_mp_api.py
退出码 0=一致，1=发现问题。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MP = ROOT.parent / "miniprogram"
sys.path.insert(0, str(ROOT))


def load_routes() -> set[tuple[str, str]]:
    """返回 {(METHOD, 路径模板)}，路径模板形如 /api/collect/{cid}/confirm。

    为什么不直接在 main.app.routes 上遍历：FastAPI 较新版本里 include_router
    会包成 `_IncludedRouter` 占位对象（不带 path/routes），顶层遍历拿不到业务路由
    —— 第一版就因此把所有接口都误报成"后端无此路由"。
    这里改为**向注册表要 router**（与 main.py 挂载用的是同一份声明），
    再按 prefix + path 精确拼装，既稳定又和实际挂载完全一致。
    """
    import main  # noqa: F401  确保 app 已构建（顺带验证导入无误）
    from routers import registry

    out: set[tuple[str, str]] = set()
    for r in registry.get_routers():
        prefix = getattr(r, "prefix", "") or ""
        for route in r.routes:
            path = getattr(route, "path", "") or ""
            # 本项目的 router 自身就带 /api 前缀（如 prefix='/api'，route='/orders'），
            # 而 registry 测试又要求 router.prefix 必须是 '/api'，
            # 所以这里不能无脑拼接（拼了会得到 /api/api/...）。
            full = path if path.startswith(prefix or "\0") else (prefix + path)
            methods = getattr(route, "methods", None) or set()
            for m in methods:
                if m in ("HEAD", "OPTIONS"):
                    continue
                out.add((m, full))
    # 直接挂在 app 上的路由（首页 / 公开收款页等）
    for route in main.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for m in methods:
            if m not in ("HEAD", "OPTIONS"):
                out.add((m, path))
    return out


def normalize(path: str) -> str:
    """把小程序的拼接路径归一成 FastAPI 的模板形式。

    '/api/backup/restore/' + encodeURIComponent(name)  →  '/api/backup/restore/{x}'
    '/api/customers/' + id + '/insight'                 →  '/api/customers/{x}/insight'
    """
    path = path.split("?")[0]
    # 模板字符串里的 ${...}
    path = re.sub(r"\$\{[^}]*\}", "{x}", path)
    # 字符串拼接的变量段：'.../' + expr + '/...'  →  '/{x}/'
    # 注意要用 '...{x}/...' 而不是 '{x}done'：早期版本把 '/done' 吃掉了，
    # 导致把正确的拼接路径误判成"方法不匹配"。
    path = re.sub(r"'\s*\+[^+']*\+\s*'", "{x}", path)
    # 结尾处只有拼接（'.../' + id）
    path = re.sub(r"'\s*\+[^+']*$", "{x}", path)
    path = re.sub(r"\{[^}]*\}", "{x}", path)
    path = re.sub(r"/+", "/", path)
    return path.rstrip("/") or "/"


def find_route(guess: str, templates: set[tuple[str, str]]):
    """按归一后的路径找后端路由：先精确，再判断"末尾是否多了变量段"。

    为什么要多一步：`'/api/x' + (cond ? '?a=' + v : '')` 这种写法里，
    问号在括号内部，解析出来会得到 `/api/x/{x}` 而实际路由是 `/api/x`。
    末尾的变量段本来就可能是查询参数拼接，所以逐级去掉末尾 {x} 再精确比对一次。
    """
    exact = [(m, p) for (m, p) in templates if p == guess]
    if exact:
        return exact

    parts = [s for s in guess.split("/") if s]
    while parts and parts[-1] == "{x}":
        parts = parts[:-1]
        cand = "/" + "/".join(parts)
        hit = [(m, p) for (m, p) in templates if p == cand]
        if hit:
            return hit

    g = [s for s in guess.split("/") if s]
    out = []
    for m, p in templates:
        parts = [s for s in p.split("/") if s]
        if len(parts) != len(g):
            continue
        ok = True
        for a, b in zip(g, parts):
            if a == "{x}" or b == "{x}":
                continue
            if a != b:
                ok = False
                break
        if ok:
            out.append((m, p))
    return out


def _split_args(argstr: str) -> list[str]:
    """按顶层逗号切分参数（跳过括号/引号内的逗号）。"""
    parts, depth, quote, cur = [], 0, None, []
    i = 0
    while i < len(argstr):
        ch = argstr[i]
        if quote:
            cur.append(ch)
            if ch == "\\":
                if i + 1 < len(argstr):
                    cur.append(argstr[i + 1])
                    i += 2
                    continue
            elif ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if cur:
        parts.append("".join(cur))
    return [p.strip() for p in parts]


def _path_from_expr(expr: str) -> str:
    """把路径表达式扫成路径模板（一次线性扫描，不靠正则猜）。

    规则：
      - 字符串字面量里的文本原样保留（模板字符串里的 ${...} 记作变量）
      - 字符串之间/之后出现 `+` → 中间或后面是动态部分，记作 {x}
      - 直接相邻的字符串（'' 'x'）不产生变量
      - 跳过括号表达式与对象字面量（三元、payload={...}），各记一个变量
    最后在 `?` 处截断：查询参数不影响路径匹配。

    为什么要这么细：这段逻辑被"正则+启发式"反复咬过 —— 三元表达式、模板字符串、
    对象字面量、encodeURIComponent(...) 各让路径多一段或少一段，
    而路径段数一错，就会把 `DELETE /api/budgets/1` 误判成 `DELETE /api/budgets`。
    """
    out: list[str] = []
    i = 0
    prev_str = False

    def mark_var():
        """记录一个变量占位（避免连续重复）。"""
        if prev_str or (out and out[-1] != "{x}"):
            out.append("{x}")

    while i < len(expr):
        ch = expr[i]
        if ch in "'\"`":
            q = ch
            j = i + 1
            buf: list[str] = []
            while j < len(expr) and expr[j] != q:
                if expr[j] == "\\" and j + 1 < len(expr):
                    buf.append(expr[j + 1])
                    j += 2
                    continue
                if (q == "`" and expr[j] == "$" and j + 1 < len(expr)
                        and expr[j + 1] == "{"):
                    buf.append("\x00")          # 模板字符串里的变量
                    j += 2
                    depth = 1
                    while j < len(expr) and depth:
                        if expr[j] == "{":
                            depth += 1
                        elif expr[j] == "}":
                            depth -= 1
                        j += 1
                    continue
                buf.append(expr[j])
                j += 1
            out.append("".join(buf))
            prev_str = True
            i = j + 1
            continue

        if ch == "+":
            nxt = expr[i + 1:].lstrip()
            if nxt[:1] not in ("'", '"', "`", ""):
                mark_var()
            prev_str = False
            i += 1
            continue

        if ch == "(":
            depth = 1
            j = i + 1
            while j < len(expr) and depth:
                if expr[j] == "(":
                    depth += 1
                elif expr[j] == ")":
                    depth -= 1
                j += 1
            mark_var()
            prev_str = False
            i = j
            continue

        if ch == "{":                            # 对象字面量，不是路径
            depth = 1
            j = i + 1
            while j < len(expr) and depth:
                if expr[j] == "{":
                    depth += 1
                elif expr[j] == "}":
                    depth -= 1
                j += 1
            prev_str = False
            i = j
            continue

        if not ch.isspace() and ch != ",":
            prev_str = False
        i += 1

    path = "".join(out).replace("\x00", "{x}")
    path = path.split("?")[0]
    path = re.sub(r"\{x\}(\{x\})+", "{x}", path)
    # 变量紧跟路径段 → 补斜杠；紧跟 ?/&/= → 属于查询参数，丢掉
    path = re.sub(r"(?<=[A-Za-z0-9_\-])\{x\}", "/{x}", path)
    path = re.sub(r"\{x\}(?=[?&=])", "", path)
    path = re.sub(r"(?<=[?&=])\{x\}", "", path)
    path = path.strip("?&=")
    path = re.sub(r"/+", "/", path).rstrip("/")
    return path


def extract_calls(js: str) -> list[tuple[str, str]]:
    """从 api.js 源码里抽出 (方法, 路径模板) 调用。"""
    calls: list[tuple[str, str]] = []
    for m in re.finditer(r"request\(", js):
        i = m.end()
        depth = 1
        while i < len(js) and depth:
            if js[i] == "(":
                depth += 1
            elif js[i] == ")":
                depth -= 1
            i += 1
        args = _split_args(js[m.end():i - 1])
        if not args:
            continue
        path = _path_from_expr(args[0])
        if not path.startswith("/"):
            continue
        method = "GET"
        for a in args[1:]:
            mm = re.match(r"['\"](GET|POST|PUT|DELETE|PATCH)['\"]", a.strip())
            if mm:
                method = mm.group(1)
                break
        calls.append((method, path))
    return calls


def main() -> int:
    """检查小程序调用的接口是否都真实存在，并列出未被使用的后端接口。

    反向清单只做提示：其中一部分是脚本解析不到的真调用（url 拼接、三元带参数），
    把它算作失败会逼着后来人"为了让检查通过"去改脚本，反而掩盖真问题。
    真问题只有一类：小程序调用了不存在的接口 / 方法不匹配。
    """
    routes = load_routes()
    route_templates = {(m, normalize(p)) for m, p in routes}

    api_js = (MP / "utils" / "api.js").read_text(encoding="utf-8")
    calls = extract_calls(api_js)

    problems: list[str] = []
    checked = 0
    for method, raw in calls:
        guess = normalize(raw)
        matches = find_route(guess, route_templates)
        if not matches:
            problems.append(f"{method} {raw} → 归一为 {guess}，后端无此路由")
            continue
        checked += 1
        methods = {m for m, _ in matches}
        if method not in methods:
            problems.append(
                f"{method} {raw}：后端该路径只支持 {sorted(methods)}（方法不匹配）")

    print(f"扫描 utils/api.js：识别 {len(calls)} 个调用，校验 {checked} 个")
    if problems:
        print("\n发现问题：")
        for p in problems:
            print("  ✗", p)
    else:
        print("✅ 小程序调用的接口路径与后端路由完全一致")

    # 反向提示：后端有、小程序没用到（不是错误，但能暴露"做了功能没入口"）
    used = {normalize(p) for _, p in calls}
    unused = sorted({p for _, p in route_templates
                     if p.startswith("/api/") and p not in used})
    if unused:
        print(f"\n后端已注册但小程序未调用（{len(unused)} 个，仅提示）：")
        for p in unused[:40]:
            print("  ·", p)
        if len(unused) > 40:
            print(f"  … 其余 {len(unused) - 40} 个省略")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
