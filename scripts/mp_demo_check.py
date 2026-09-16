# -*- coding: utf-8 -*-
"""小程序演示前自检：后端接口契约 + 页面静态一致性。

演示前跑一遍，能在打开微信开发者工具之前就发现：
  1. 小程序调用的接口后端是否存在（OpenAPI schema 为权威契约）
  2. WXML 绑定的事件方法 / 变量在 JS 里是否定义
  3. 自定义组件是否在页面 json 里注册
  4. 是否还有把错误吞掉的空 catch（演示时表现为静默失败）

用法（在 server 目录下）：
    python ../scripts/mp_demo_check.py
"""
import json
import re
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
MP = Path(__file__).resolve().parent.parent / "miniprogram"
sys.path.insert(0, str(SERVER))

problems: list[str] = []
notes: list[str] = []


# ---------------- 1. 接口契约 ----------------
def check_contract() -> None:
    print("=" * 72)
    print("1. 小程序调用的接口 × 后端 OpenAPI 契约")
    print("=" * 72)

    import main  # noqa: E402  需要 server 在 sys.path 上

    backend: dict[str, set[str]] = {}
    for path, ops in main.app.openapi().get("paths", {}).items():
        for m in ops:
            if m.upper() in ("HEAD", "OPTIONS"):
                continue
            backend.setdefault(m.upper(), set()).add(path)

    src = (MP / "utils" / "api.js").read_text(encoding="utf-8")
    calls: list[tuple[str, str]] = []

    # 用括号匹配精确切出 request(...) 的参数，再按顶层逗号分成 [路径, 方法]。
    # 之前用「取所有字符串字面量拼接」的做法会把方法参数 'POST' 也拼进路径，
    # 正则逐层打补丁只会越修越错，所以这里直接做括号配对。
    def split_args(inner: str) -> list[str]:
        args, depth, quote, cur = [], 0, "", ""
        for ch in inner:
            if quote:
                cur += ch
                if ch == quote:
                    quote = ""
                continue
            if ch in "'\"`":
                quote = ch
                cur += ch
                continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            if ch == "," and depth == 0:
                args.append(cur)
                cur = ""
            else:
                cur += ch
        if cur.strip():
            args.append(cur)
        return args

    def path_of(expr: str) -> str:
        """从路径表达式还原可比较的路径（单趟扫描，不靠正则堆叠）。

        规则：
          - 字符串字面量里的文本保留；模板串里的 ${...} 记作变量
          - 字符串之间/之后的 `+` 变量记作 {p}
          - **括号里的内容整体忽略**：那里几乎都是查询参数的条件拼接
            （如 `(status ? '?status=' + status : '')`）。早期版本把括号里的
            标识符也当路径段，于是 `/api/collect/list` 被还原成
            `/api/collect/list{p}`，误报"接口缺失"。
          - `?` 之后全部丢弃（查询参数不参与路径匹配）
        """
        out: list[str] = []
        i = 0
        prev_str = False
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
                    if q == "`" and expr[j] == "$" and j + 1 < len(expr) \
                            and expr[j + 1] == "{":
                        buf.append("{p}")
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
            if ch == "(":
                depth = 1
                j = i + 1
                while j < len(expr) and depth:
                    if expr[j] == "(":
                        depth += 1
                    elif expr[j] == ")":
                        depth -= 1
                    j += 1
                # 括号整体丢弃，**不补占位符**：
                #   '/api/collect/list' + (status ? '?status=' + status : '')
                # 里括号装的是查询参数逻辑；补了 {p} 就变成 /api/collect/list{p}，
                # 会被误判成"接口缺失"（实测）。真正的路径变量都在括号外。
                prev_str = False
                i = j
                continue
            if ch == "+":
                prev_str = False
                i += 1
                continue
            if ch == "{":                       # 对象字面量，不是路径
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
            if not ch.isspace() and ch != "," and ch not in "[]":
                # 裸标识符/数字：可能是查询串的一部分，也可能是路径变量
                m = re.match(r"[A-Za-z_$][\w$.]*|\d+", expr[i:])
                if m:
                    name = m.group(0)
                    if "." not in name and name not in ("api", "request"):
                        if not prev_str:
                            out.append("{p}")
                    prev_str = False
                    i += len(name)
                    continue
                prev_str = False
            i += 1

        joined = "".join(out)
        if "/api/" not in joined:
            return ""
        joined = joined.split("?")[0]
        joined = re.sub(r"\{p\}(?=[?&=])", "", joined)
        joined = re.sub(r"(?<=[?&=])\{p\}", "", joined)
        joined = joined.strip("?&=")
        joined = re.sub(r"\{p\}(\{p\})+", "{p}", joined)
        joined = re.sub(r"/+", "/", joined).rstrip("/")
        return joined

    for line in src.splitlines():
        if not re.search(r"(?<![\w.])request\(", line):
            continue
        head = line.split("request(", 1)[1]
        # 括号配对：request( 已经消费掉一个 '('，这里从深度 1 开始找配对的 ')'
        depth, end = 1, None
        for i, ch in enumerate(head):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        inner = head[:end] if end is not None else head
        args = split_args(inner)
        if not args:
            continue
        path = path_of(args[0])
        if not path:
            continue
        method = "GET"
        if len(args) > 1:
            m = re.search(r"[`'](\w+)[`']", args[1])
            if m:
                method = m.group(1)
        calls.append((method, path))

    # reportUrl 走 wx.downloadFile，不经 request
    ru = re.search(r"reportUrl:.*?`([^`]+)`", src)
    if ru:
        calls.append(("GET", ru.group(1).split("?")[0]))

    def norm(p: str) -> str:
        """统一动态段：模板变量、拼接参数、已写死的数字都归一为 {p}。"""
        p = p.split("?")[0].rstrip("/")
        p = re.sub(r"\$\{[^}]+\}", "{p}", p)
        p = re.sub(r"\{[^}]+\}", "{p}", p)
        p = re.sub(r"/\d+$", "/{p}", p)
        return p

    def norm_backend(p: str) -> str:
        p = p.rstrip("/")
        return re.sub(r"\{[^}]+\}", "{p}", p)

    missing = []
    for method, raw in calls:
        want = norm(raw)
        ok = any(norm_backend(bp) == want for bp in backend.get(method, set()))
        if not ok:
            missing.append((method, raw))

    print(f"  小程序调用接口数：{len(calls)}    后端接口数：{sum(len(v) for v in backend.values())}")
    if missing:
        for method, raw in missing:
            problems.append(f"接口缺失：{method} {raw}")
            print(f"  ❌ {method:6} {raw}")
    else:
        print("  ✅ 全部接口在后端存在")


# ---------------- 2. 页面静态一致性 ----------------
BUILTIN_VARS = {"true", "false", "null", "undefined", "item", "index", "wx", "Math", "JSON"}
BUILTIN_TAGS = {
    "view", "text", "button", "input", "image", "scroll-view", "swiper", "swiper-item",
    "picker", "picker-view", "checkbox", "checkbox-group", "radio", "radio-group", "label",
    "form", "textarea", "navigator", "icon", "progress", "slider", "switch", "canvas",
    "video", "audio", "map", "cover-view", "cover-image", "rich-text", "web-view",
    "movable-area", "movable-view", "open-data", "block", "template", "import", "include",
    "wxs", "camera", "live-player", "live-pusher", "ad",
}


def check_pages() -> None:
    print()
    print("=" * 72)
    print("2. 页面静态一致性（WXML 绑定 × JS 定义）")
    print("=" * 72)

    pages = sorted(p for p in (MP / "pages").iterdir() if p.is_dir())
    for page in pages:
        name = page.name
        wxml_f, js_f, json_f = page / f"{name}.wxml", page / f"{name}.js", page / f"{name}.json"
        if not (wxml_f.exists() and js_f.exists()):
            problems.append(f"{name}: 缺少 wxml 或 js")
            continue
        wxml = wxml_f.read_text(encoding="utf-8")
        js = js_f.read_text(encoding="utf-8")

        # 事件绑定 → JS 方法
        handlers = {m.group(1).strip()
                    for m in re.finditer(r'\b(?:bind|catch)[:\w]*\s*=\s*"([^"{}]+)"', wxml)}
        defined = set(re.findall(r"^\s{2}([A-Za-z_$][\w$]*)\s*\(", js, re.M))
        defined |= set(re.findall(r"^\s{2}([A-Za-z_$][\w$]*)\s*:\s*(?:function|\()", js, re.M))
        for comp in (MP / "components").iterdir():
            if comp.is_dir() and (comp / "index.js").exists():
                defined |= set(re.findall(
                    r"^\s{2}([A-Za-z_$][\w$]*)\s*\(",
                    (comp / "index.js").read_text(encoding="utf-8"), re.M))
        miss = sorted(h for h in handlers if h not in defined and not h.startswith("data-"))
        if miss:
            problems.append(f"{name}: WXML 绑定但 JS 未定义的方法 {miss}")
            print(f"  ❌ {name}: 未定义方法 {miss}")

        # 自定义组件注册
        declared = set()
        if json_f.exists():
            try:
                declared = set(json.loads(json_f.read_text(encoding="utf-8"))
                               .get("usingComponents", {}))
            except json.JSONDecodeError:
                problems.append(f"{name}: {name}.json 非法 JSON")
        used = {t for t in re.findall(r"<([a-z][\w-]*)\s", wxml)
                if t not in BUILTIN_TAGS and not t.startswith("wx-")}
        unreg = sorted(used - declared)
        if unreg:
            problems.append(f"{name}: 未注册的自定义组件 {unreg}")
            print(f"  ❌ {name}: 未注册组件 {unreg}")

        # 空 catch：错误被吞掉，演示时静默失败
        empty = len(re.findall(r"\.catch\(\s*\(\s*\)\s*=>\s*\{\s*\}\s*\)", js))
        if empty:
            notes.append(f"{name}: 有 {empty} 处空 catch（失败时无提示）")

    print(f"  检查页面数：{len(pages)}")
    if not any(p.startswith(tuple(pages and [x.name for x in pages])) for p in problems):
        print("  ✅ 事件绑定与组件注册无问题")
    for n in notes:
        print(f"  ⚠ {n}")


# ---------------- 3. 演示数据检查（实现见文件末尾 check_data） ----------------
# ---------------- 4. 演示配置检查（此处实现） ----------------
def check_demo_config() -> None:
    print()
    print("=" * 72)
    print("4. 演示配置检查")
    print("=" * 72)

    app_json = json.loads((MP / "app.json").read_text(encoding="utf-8"))
    if not app_json.get("plugins"):
        notes.append("app.json 未声明 plugins：语音（同声传译）不可用，将降级为手动输入")
        print("  ⚠ app.json 未声明 plugins → 语音记账不可用（界面会提示改用手动输入）")

    pc = json.loads((MP / "project.config.json").read_text(encoding="utf-8"))
    if pc.get("appid") == "touristappid":
        notes.append("appid 为 touristappid：插件/真机能力受限，仅够演示基础流程")
        print("  ⚠ project.config.json 的 appid 是 touristappid（游客模式）")
    if not pc.get("setting", {}).get("urlCheck") is False:
        notes.append("未关闭 urlCheck：真机连局域网 http 地址会被拦")
        print("  ⚠ urlCheck 未设为 false，真机连 http://局域网IP 可能被拦")
    else:
        print("  ✅ urlCheck 已关闭（真机可连局域网 http 地址）")

    app_js = (MP / "app.js").read_text(encoding="utf-8")
    m = re.search(r"baseUrl:\s*'([^']+)'", app_js)
    if m:
        print(f"  ℹ 默认后端地址：{m.group(1)}")
        print("    真机演示请在「设置」页改成电脑的局域网 IP（127.0.0.1 在手机上指向手机自己）")


def check_data() -> None:
    """第 3 部分：演示数据是否就绪。

    演示最多只录入几条数据，账本/熟客/提醒页要靠预置数据才有内容。
    忘了灌数据时，现场打开熟客页是空的、单店模型反推的日销会低得难看。
    这里直接在打开开发者工具之前把这种情况报出来。
    """
    print()
    print("=" * 72)
    print("3. 演示数据是否已灌入")
    print("=" * 72)

    try:
        import db  # noqa: PLC0415  需要 server 在 sys.path 上
    except Exception as e:  # noqa: BLE001
        notes.append(f"无法导入 db 模块，跳过数据检查：{e}")
        print(f"  ⚠ 无法导入 db：{e}")
        return

    db_path = db.DB_PATH
    if not Path(db_path).exists():
        problems.append("演示数据库不存在，请先执行第 7 步灌入演示数据")
        print(f"  ❌ 数据库不存在：{db_path}")
        print("     修复：cd server && python ..\\scripts\\seed_demo_data.py")
        return

    def count(sql: str) -> int:
        try:
            with db.get_conn() as conn:
                return int(conn.execute(sql).fetchone()[0] or 0)
        except Exception:  # noqa: BLE001
            return 0

    txn = count("SELECT COUNT(*) FROM transactions")
    cust = count("SELECT COUNT(*) FROM customers")
    mem = count("SELECT COUNT(*) FROM memories")
    rem = count("SELECT COUNT(*) FROM reminders WHERE done=0")
    with_cust = count("SELECT COUNT(*) FROM transactions WHERE customer_id IS NOT NULL")

    # 最低要求：熟客页与提醒页要有内容，账本页要有流水可展示
    checks = [
        (txn >= 20, f"流水 {txn} 笔", "少于 20 笔，账本页会很空"),
        (cust >= 5, f"熟客 {cust} 人", "少于 5 人，熟客列表撑不起来"),
        (mem >= 3, f"记忆点 {mem} 条", "少于 3 条，熟客详情没有内容可讲"),
        (rem >= 1, f"待办提醒 {rem} 条", "没有待办，提醒区显示「还没有提醒」"),
        (with_cust >= 5, f"关联熟客的消费记录 {with_cust} 笔",
         "熟客详情里看不到消费记录"),
    ]
    print(f"  数据库：{db_path}")
    for ok, label, hint in checks:
        print(f"  {'✅' if ok else '❌'} {label}" + ("" if ok else f"  → {hint}"))
    if not all(ok for ok, _, _ in checks):
        problems.append("演示数据不完整（见第 3 部分），请执行第 7 步灌入演示数据")

    # 反推日销：太低会让单店模型演示出「危险」结论
    try:
        import store  # noqa: F401,PLC0415
        stats = db.store_ledger_stats()
        dr = stats.get("daily_revenue")
        gm = stats.get("gross_margin")
        if dr:
            print(f"  ℹ 从账本反推：日销 {dr:,.0f} 元 / "
                  f"毛利率 {'—' if gm is None else format(gm, '.1%')}")
            if dr < 600:
                notes.append(f"反推日销仅 {dr:,.0f} 元，单店模型可能演示出「危险」结论")
                print("     ⚠ 日销偏低，单店模型那一站效果会打折（可重灌演示数据）")
    except Exception:  # noqa: BLE001
        pass

    # 缓存预热状态：预热过则现场秒出
    warmed = []
    for domain, key, label in (("ledger", None, "经营洞察"),
                               ("tax", None, "报税建议")):
        try:
            with db.get_conn() as conn:
                n = conn.execute(
                    "SELECT COUNT(*) FROM domain_context WHERE domain=? AND value!=''",
                    (domain,)).fetchone()[0]
            if n:
                warmed.append(label)
        except Exception:  # noqa: BLE001
            pass
    if warmed:
        print(f"  ✅ 已预热缓存：{'、'.join(warmed)}（现场会秒出）")
    else:
        notes.append("尚未预热 AI 内容缓存，现场首次生成要等 5~65 秒（见第 8 步）")
        print("  ⚠ 尚未预热缓存 → 现场首次生成经营洞察/报税建议要等几十秒")
        print("     可选：按教程第 8 步预热，现场即可秒出")


print("小程序演示前自检")
check_contract()
check_pages()
check_data()
check_demo_config()

print()
print("=" * 72)
print("汇总")
print("=" * 72)
print(f"  问题（必须修）：{len(problems)}")
for p in problems:
    print(f"    ❌ {p}")
print(f"  提示（需注意）：{len(notes)}")
for n in notes:
    print(f"    ⚠ {n}")
sys.exit(1 if problems else 0)
