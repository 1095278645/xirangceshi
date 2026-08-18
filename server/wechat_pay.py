# ========== 微信支付 v3 交易账单同步 ==========
"""把微信支付商户号的收款流水自动拉进账本。

两种模式：
1. 真实对接（mchid 为真实商户号）：
   需要商户号 + 商户 API 证书（apiclient_cert.pem）/ 私钥（apiclient_key.pem）+ APIv3 密钥。
   通过 /v3/bill/tradebill 拉取按日交易账单（tar.gz → CSV），解析后入库。
   依赖 wechatpayv3（pip install wechatpayv3）。

2. 演示模式（mchid 填 DEMO）：
   无需任何商户资料，直接生成演示账单体验「流水自动入库」全流程。
   数据带 [演示] 标记，可随时一键清空（POST /api/payment/demo-clear）。
"""
import io
import re
import tarfile
import csv
import random
import logging
from datetime import date, datetime, timedelta

from categories import FRIENDLY_NAMES

log = logging.getLogger("wechat_pay")

CATEGORY = "主营业务收入"          # 微信收款默认入主营
FRIENDLY = FRIENDLY_NAMES.get(CATEGORY, CATEGORY)

# 微信账单 CSV 标题行里可能出现的金额列名（按实际版本匹配）
_AMOUNT_COL_RE = re.compile(r"(订单金额|应结订单金额|交易金额|收入金额|金额)\s*\(?元?\)?")


def _parse_csv_text(text: str):
    """解析微信交易账单 CSV 文本 → [ {字段:值}, ... ]
    兼容不同版本的表头顺序：按标题行动态映射列名。"""
    lines = text.replace("\r\n", "\n").split("\n")
    header_idx = None
    for i, ln in enumerate(lines):
        if "交易时间" in ln and ("交易类型" in ln or "微信订单号" in ln):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("账单格式无法识别（未找到表头行）")
    header = [h.strip() for h in lines[header_idx].split(",")]

    rows = []
    for ln in lines[header_idx + 1:]:
        ln = ln.strip()
        if not ln or ln.startswith("总") or ln.startswith("----"):
            continue
        # 数据行以反引号开头（微信账单的转义约定，字段内逗号用 ` 包裹）
        if ln.startswith("`"):
            ln = ln[1:]
        parts = _split_csv_line(ln)
        if len(parts) < len(header):
            parts += [""] * (len(header) - len(parts))
        rows.append(dict(zip(header, [p.strip() for p in parts[:len(header)]])))
    return rows


def _split_csv_line(ln: str):
    """按微信账单约定拆分：字段内逗号用反引号包裹 → 转成标准引号形式再交给 csv 解析。"""
    quoted = re.sub(r"`([^`]*)`", r'"\1"', ln)
    return list(csv.reader([quoted]))[0]


def _to_yuan(raw):
    """金额字段 → 元（微信账单金额单位为元，宽松兼容分）。"""
    raw = (raw or "").strip().replace(",", "")
    if not raw:
        return 0.0
    try:
        v = float(raw)
    except ValueError:
        return 0.0
    # 疑似以“分”为单位的整数（如 1200 表示 12 元）→ 转元
    if v > 0 and float(v).is_integer() and v >= 100 and "." not in raw:
        return round(v / 100, 2)
    return round(v, 2)


def _row_to_txn(row):
    """账单行 → 账本交易 dict。返回 None 表示该行忽略（非成功交易/无金额）。"""
    status = row.get("交易状态") or row.get("状态") or ""
    if status and status.upper() != "SUCCESS":
        return None
    amount = _to_amount(row)
    if amount <= 0:
        return None
    item = (row.get("商品名称") or "微信收款").strip()
    wx_id = (row.get("微信订单号") or "").strip()
    txn_time = (row.get("交易时间") or "").strip()
    return {
        "item": item[:80],
        "amount": amount,
        "note": f"微信收款{(' · ' + txn_time) if txn_time else ''}",
        "wx_trade_id": wx_id or (row.get("商户订单号") or "").strip(),
        "created_at": _norm_datetime(txn_time),
    }


def _to_amount(row):
    for k in row:
        if _AMOUNT_COL_RE.search(k) and row[k]:
            v = _to_yuan(row[k])
            if v:
                return v
    return 0.0


def _norm_datetime(txt):
    """账单时间 '2026-06-17 20:12:33' → 同格式；空则用今天。"""
    txt = (txt or "").strip()
    if not txt:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(txt, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return txt


# ---------------- 真实模式：拉取并解析 ----------------
def _fetch_real_bill(cfg, bill_date):
    """通过微信支付 v3 拉取指定日期（YYYY-MM-DD）交易账单 → 交易行列表"""
    try:
        from wechatpayv3 import WeChatPay
    except ImportError:
        raise RuntimeError("正式对接需要先安装 wechatpayv3：pip install wechatpayv3")

    if not (cfg.get("mchid") and cfg.get("private_key_path") and cfg.get("api_v3_key")):
        raise RuntimeError("商户号/私钥路径/APIv3密钥 未配置完整")

    # 商户证书序列号（从 PEM 证书解析）
    from cryptography import x509
    with open(cfg["cert_path"], "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    serial = format(cert.serial_number, "x")

    wxpay = WeChatPay(
        mchid=cfg["mchid"],
        cert_serial_no=serial,
        private_key=open(cfg["private_key_path"]).read(),
        apiv3_key=cfg["api_v3_key"],
        appid=cfg.get("appid") or "",
    )
    ymd = bill_date.replace("-", "")
    resp = wxpay.get(f"/v3/bill/tradebill?bill_date={ymd}&bill_type=ALL")
    download_url = resp.get("download_url")
    if not download_url:
        raise RuntimeError("微信未返回账单下载地址（可能是当日无交易或商户状态异常）")

    import requests
    r = requests.get(download_url, timeout=60)
    r.raise_for_status()
    raw = r.content

    # 账单为 tar.gz（内含 CSV）；兼容直接 CSV 的情况
    text = None
    try:
        tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
        for m in tf.getmembers():
            if m.isfile():
                text = tf.extractfile(m).read()
                break
    except tarfile.TarError:
        text = raw
    if text is None:
        raise RuntimeError("账单解压失败")
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return _parse_csv_text(text.decode(enc))
        except (UnicodeDecodeError, ValueError):
            continue
    raise RuntimeError("账单内容解析失败")


# ---------------- 演示模式 ----------------
def _demo_bill(cfg, bill_date):
    """无商户号时生成演示账单，体验「自动入账本」全流程。
    以日期为随机种子：同一天生成的账单完全一致，保证重复同步幂等可验证。"""
    d = datetime.strptime(bill_date, "%Y-%m-%d")
    rng = random.Random(d.strftime("%Y%m%d"))   # 固定种子 → 同一天账单可复现
    n = rng.randint(5, 8)
    rows = []
    for i in range(n):
        t = d.replace(hour=rng.randint(7, 21), minute=rng.randint(0, 59),
                      second=rng.randint(0, 59))
        rows.append({
            "item": "微信收款（演示）",
            "amount": round(rng.uniform(5, 80), 2),
            "note": "演示数据 · 账单同步演示（可一键清空）",
            "wx_trade_id": f"DEMO-{bill_date}-{i + 1}",
            "created_at": t.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return rows


# ---------------- 统一入口 ----------------
def fetch_trade_bill(cfg, bill_date):
    """按模式拉取账单 → 统一交易行列表 [{item, amount, note, wx_trade_id, created_at}]。
    演示模式：mchid 为 DEMO（不分大小写）；否则走微信支付 v3 真实接口。"""
    bill_date = bill_date or (date.today() - timedelta(days=1)).isoformat()
    if str(cfg.get("mchid") or "").upper() == "DEMO":
        return _demo_bill(cfg, bill_date)
    raw_rows = _fetch_real_bill(cfg, bill_date)
    txns = []
    for row in raw_rows:
        t = _row_to_txn(row)
        if t:
            txns.append(t)
    return txns