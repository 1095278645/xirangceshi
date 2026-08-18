"""税法计算与安全护栏模块（源自省账通税法规则 2026）

提供：小规模增值税、附加税（六税两费减半）、个人所得税（7级超额累进）、
企业所得税（小微企业分段）、报税日历，以及记账安全护栏（大额检测 + 边界场景）。
规则参数直接内嵌为 Python 常量，不依赖外部 YAML，开箱即用。
"""
from datetime import date

# ========== 增值税（小规模纳税人） ==========
VAT_RATE = 0.03
VAT_EXEMPTION_QUARTERLY = 300_000   # 季度销售额≤30万免征

# ========== 附加税 ==========
SURTAX_ITEMS = [
    {"name": "城市维护建设税", "rate": 0.07},
    {"name": "教育费附加", "rate": 0.03},
    {"name": "地方教育附加", "rate": 0.02},
]
SIX_TAX_RELIEF = 0.50   # 六税两费减半（小规模纳税人适用）

# ========== 个人所得税（工资薪金，7级超额累进） ==========
PIT_EXEMPTION_THRESHOLD = 5000
# (下限, 上限, 税率, 速算扣除数)
PIT_BANDS = [
    (0, 3000, 0.03, 0),
    (3000, 12000, 0.10, 210),
    (12000, 25000, 0.20, 1410),
    (25000, 35000, 0.25, 2660),
    (35000, 55000, 0.30, 4410),
    (55000, 80000, 0.35, 7160),
    (80000, float("inf"), 0.45, 15160),
]

# ========== 企业所得税 ==========
CIT_STANDARD_RATE = 0.25
CIT_SMALL_INCOME_LIMIT = 3_000_000
# 小微企业有效税率分段（2023-2027 年政策延续口径）
CIT_SMALL_BANDS = [
    (0, 1_000_000, 0.05),
    (1_000_000, 3_000_000, 0.10),
]

# ========== 报税日历 ==========
FILING_MONTHLY_DEADLINE = 15
FILING_QUARTERLY_MONTHS = (1, 4, 7, 10)
FILING_ANNUAL_RECON_MONTH = 5   # 5月31日：企业所得税年度汇算清缴截止

# ========== 安全护栏：大额检测 ==========
LARGE_AMOUNT_CONFIRM = 50_000     # 单笔 >5万：需确认后再入账
LARGE_AMOUNT_ADVISOR = 500_000    # 单笔 >50万：强烈建议咨询专业会计

# ========== 安全护栏：10种边界场景 ==========
BOUNDARY_SCENARIOS = [
    {"code": "B01", "scene": "工商注册/变更", "level": "中高",
     "keywords": ("注册", "营业执照", "变更登记"), "advice": "需走工商流程，涉及章程、验资材料"},
    {"code": "B02", "scene": "税务稽查", "level": "极高",
     "keywords": ("稽查", "税务检查", "补税"), "advice": "按稽查通知书配合，保留账册凭证原件"},
    {"code": "B03", "scene": "股权转让", "level": "极高",
     "keywords": ("股权", "转让", "股东"), "advice": "需股权转让协议、评估报告与完税证明"},
    {"code": "B04", "scene": "投资理财", "level": "高",
     "keywords": ("投资", "理财", "基金", "股票"), "advice": "先做风险测评，确认收益属性与税务处理"},
    {"code": "B05", "scene": "跨境业务", "level": "高",
     "keywords": ("外币", "汇率", "进出口", "海关"), "advice": "涉及结售汇与海关单据，需专项处理"},
    {"code": "B06", "scene": "融资贷款", "level": "中高",
     "keywords": ("贷款", "融资", "抵押"), "advice": "签订贷款/抵押合同，留意利息税前扣除凭证"},
    {"code": "B07", "scene": "劳动纠纷", "level": "中",
     "keywords": ("劳动仲裁", "工伤", "赔偿"), "advice": "保留劳动合同、考勤与工资发放记录"},
    {"code": "B08", "scene": "知识产权", "level": "中",
     "keywords": ("专利", "商标", "侵权"), "advice": "保留注册/授权文件，侵权需律师介入"},
    {"code": "B09", "scene": "诉讼仲裁", "level": "极高",
     "keywords": ("起诉", "仲裁", "法院"), "advice": "诉讼时效与证据保全，建议尽快请律师"},
    {"code": "B10", "scene": "年度汇算", "level": "中高",
     "keywords": ("汇算清缴", "年度申报"), "advice": "汇算前核对账目、发票与费用凭证"},
]


# ---------------- 增值税（小规模） ----------------
def calc_vat(quarterly_revenue: float) -> dict:
    """小规模纳税人增值税：季度销售额≤30万免征，超过按征收率 3% 价税分离"""
    revenue = max(quarterly_revenue or 0, 0)
    if revenue <= VAT_EXEMPTION_QUARTERLY:
        return {
            "tax_type": "增值税（小规模）",
            "quarterly_revenue": revenue,
            "rate": VAT_RATE,
            "vat": 0,
            "exempt": True,
            "note": "季度销售额≤30万元，免征增值税",
        }
    vat = revenue / (1 + VAT_RATE) * VAT_RATE
    return {
        "tax_type": "增值税（小规模）",
        "quarterly_revenue": revenue,
        "rate": VAT_RATE,
        "vat": round(vat, 2),
        "exempt": False,
        "note": f"季度销售额 {revenue:,.0f} 元超过 30 万元免征线",
    }


# ---------------- 附加税 ----------------
def calc_surtax(vat_actual: float, is_small: bool = True) -> dict:
    """附加税：城建7% + 教育3% + 地方教育2%，小规模纳税人六税两费减半"""
    base = max(vat_actual or 0, 0)
    reduction = (1 - SIX_TAX_RELIEF) if (is_small and vat_actual) else 1.0
    items, total = [], 0.0
    for it in SURTAX_ITEMS:
        tax = base * it["rate"] * reduction
        items.append({
            "name": it["name"],
            "rate": it["rate"],
            "amount": round(tax, 2),
            "reduced": reduction < 1,
        })
        total += tax
    return {
        "tax_type": "附加税",
        "vat_base": round(base, 2),
        "items": items,
        "total": round(total, 2),
        "six_tax_relief": reduction < 1,
        "note": "已享受六税两费减半" if reduction < 1 else "未享受减免（非小规模纳税人）",
    }


# ---------------- 个人所得税 ----------------
def calc_individual_income_tax(monthly_salary: float, social_insurance: float = 0,
                                special_deduction: float = 0) -> dict:
    """工资薪金个税：应纳税所得额 = 收入 - 5000 - 社保 - 专项附加扣除，7级超额累进"""
    salary = max(monthly_salary or 0, 0)
    social = max(social_insurance or 0, 0)
    special = max(special_deduction or 0, 0)
    taxable = salary - PIT_EXEMPTION_THRESHOLD - social - special

    if taxable <= 0:
        return {
            "tax_type": "个人所得税",
            "monthly_salary": salary,
            "threshold": PIT_EXEMPTION_THRESHOLD,
            "social_insurance": social,
            "special_deduction": special,
            "taxable": 0,
            "tax": 0,
            "effective_rate": 0,
            "note": "应纳税所得额≤0，无需缴税",
        }

    for lo, hi, rate, deduction in PIT_BANDS:
        if lo < taxable <= hi:
            tax = max(taxable * rate - deduction, 0)
            return {
                "tax_type": "个人所得税",
                "monthly_salary": salary,
                "threshold": PIT_EXEMPTION_THRESHOLD,
                "social_insurance": social,
                "special_deduction": special,
                "taxable": round(taxable, 2),
                "rate": rate,
                "quick_deduction": deduction,
                "tax": round(tax, 2),
                "effective_rate": round(tax / salary * 100, 2) if salary else 0,
                "note": "7级超额累进税率",
            }
    return {"error": "税率计算异常"}


# ---------------- 企业所得税 ----------------
def calc_corporate_income_tax(annual_income: float, is_small: bool = True) -> dict:
    """企业所得税：小微企业按 5%/10% 分段；否则按 25% 标准税率"""
    income = max(annual_income or 0, 0)
    if not is_small:
        tax = income * CIT_STANDARD_RATE
        return {
            "tax_type": "企业所得税",
            "annual_income": income,
            "rate": CIT_STANDARD_RATE,
            "tax": round(tax, 2),
            "is_small": False,
            "note": "一般企业适用 25% 标准税率",
        }

    details, remaining, tax = [], income, 0
    for lo, hi, rate in CIT_SMALL_BANDS:
        if remaining <= 0:
            break
        width = hi - lo
        taxable_in_band = min(remaining, width)
        tax_in_band = taxable_in_band * rate
        tax += tax_in_band
        details.append({
            "range": f"{lo:,}-{hi:,}",
            "taxable": taxable_in_band,
            "rate": rate,
            "tax": round(tax_in_band, 2),
        })
        remaining -= taxable_in_band
    return {
        "tax_type": "企业所得税（小微企业）",
        "annual_income": income,
        "is_small": True,
        "details": details,
        "total_tax": round(tax, 2),
        "effective_rate": round(tax / income * 100, 2) if income else 0,
        "note": "年应纳税所得额≤300万元的小微企业，分段享受优惠税率",
    }


# ---------------- 报税日历 ----------------
def get_filing_calendar(year: int | None = None, month: int | None = None) -> dict:
    """当月报税日历：增值税/个税月度申报，季度申报企业税，5月汇算清缴提醒"""
    today = date.today()
    year = year or today.year
    month = month or today.month
    deadline = FILING_MONTHLY_DEADLINE
    is_quarterly = month in FILING_QUARTERLY_MONTHS

    reminders = [
        {"tax_type": "增值税", "deadline": f"{year}-{month:02d}-{deadline:02d}",
         "note": "月度申报（小规模纳税人按季度申报）"},
        {"tax_type": "个人所得税", "deadline": f"{year}-{month:02d}-{deadline:02d}",
         "note": "工资薪金代扣代缴"},
    ]
    if is_quarterly:
        reminders.append({"tax_type": "企业所得税", "deadline": f"{year}-{month:02d}-{deadline:02d}",
                          "note": "季度预缴申报"})
    if month == FILING_ANNUAL_RECON_MONTH:
        reminders.append({"tax_type": "企业所得税", "deadline": f"{year}-{month:02d}-{FILING_ANNUAL_RECON_MONTH:02d}-31",
                          "note": "年度汇算清缴截止"})
    return {"year": year, "month": month, "reminders": reminders}


# ---------------- 安全护栏 ----------------
def check_amount_guard(amount) -> dict | None:
    """大额检测：返回警示信息（None 表示无需提示）"""
    if amount is None:
        return None
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        return None
    if amt > LARGE_AMOUNT_ADVISOR:
        return {"level": "high", "message": f"单笔 {amt:,.0f} 元超过 50 万，强烈建议咨询专业会计后再入账"}
    if amt > LARGE_AMOUNT_CONFIRM:
        return {"level": "warn", "message": f"单笔 {amt:,.0f} 元超过 5 万，请确认金额无误再入账"}
    return None


def detect_boundary(text: str) -> dict | None:
    """检测记账文本是否落入10种边界场景（注册/稽查/股权/投资等），命中则返回提示"""
    if not text:
        return None
    for sc in BOUNDARY_SCENARIOS:
        if any(k in text for k in sc["keywords"]):
            return {
                "code": sc["code"],
                "scene": sc["scene"],
                "level": sc["level"],
                "advice": sc["advice"],
                "message": f"这笔业务可能涉及「{sc['scene']}」，超出日常记账范围，建议咨询专业会计后再入账。",
            }
    return None