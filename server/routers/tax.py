"""税法计算（省账通能力）+ 科目表台账"""
from fastapi import APIRouter

import ai
import db
import tax as taxcalc
from categories import ACCOUNT_CATEGORY_NAMES, ACCOUNT_TITLES
from schemas import CitIn, PitIn, SurtaxIn, VatIn

router = APIRouter(prefix="/api", tags=["tax"])


@router.get("/account-titles")
def account_titles():
    """小企业会计准则 68 科目表，按类别分组"""
    by_cat: dict[str, list] = {}
    for code, name, cat, _direction, level in ACCOUNT_TITLES:
        by_cat.setdefault(cat, []).append({"code": code, "name": name, "level": level})
    return {
        "total": len(ACCOUNT_TITLES),
        "categories": [
            {"category": cat, "name": ACCOUNT_CATEGORY_NAMES.get(cat, cat), "titles": items}
            for cat, items in by_cat.items()
        ],
    }


@router.post("/tax/vat")
def tax_vat(data: VatIn):
    """增值税（小规模）：季度销售额≤30万免征"""
    return taxcalc.calc_vat(data.quarterly_revenue)


@router.post("/tax/surtax")
def tax_surtax(data: SurtaxIn):
    """附加税：城建+教育+地方教育，小规模六税两费减半"""
    return taxcalc.calc_surtax(data.vat, data.is_small)


@router.post("/tax/pit")
def tax_pit(data: PitIn):
    """个人所得税：工资薪金 7级超额累进"""
    return taxcalc.calc_individual_income_tax(
        data.salary, data.social_insurance, data.special_deduction)


@router.post("/tax/cit")
def tax_cit(data: CitIn):
    """企业所得税：小微企业分段（5%/10%），否则 25%"""
    return taxcalc.calc_corporate_income_tax(data.annual_income, data.is_small)


@router.get("/tax/calendar")
def tax_calendar(year: int | None = None, month: int | None = None):
    """当月报税日历提醒"""
    return taxcalc.get_filing_calendar(year, month)


def _advice_cache_key(quarterly_revenue: float) -> str:
    """报税建议按销售额分桶缓存。

    分桶粒度取 1000 元：
      - 太细（按分/按元）会导致每次改一个数字都未命中，等同没有缓存；
      - 太粗会把不同销售额的建议混用（增值税额不同，建议内容就不同）。
    1000 元档对"季度销售额"这个量级足够贴近，且演示常用的整数
    （300000 / 350000 / 400000）都能命中。
    """
    bucket = int(quarterly_revenue // 1000) * 1000
    return f"quarterly_advice:{bucket}"


@router.post("/tax/advice")
def tax_advice(data: VatIn):
    """AI 报税建议（按销售额分桶缓存，默认命中缓存秒回）。

    前端在「算增值税」之后会自动请求本接口。原实现每次都真调 AI
    （实测 30~65 秒），且缓存键不区分销售额，换个数会把上一条覆盖并
    显示成新输入的结论。现在：命中同档缓存直接返回，refresh=true 才重新生成。
    """
    vat_result = taxcalc.calc_vat(data.quarterly_revenue)
    cache_key = _advice_cache_key(data.quarterly_revenue)

    if not data.refresh:
        hit = db.get_domain_context("tax", cache_key)
        if hit and (hit.get("value") or "").strip():
            return {"advice": hit["value"], "vat_result": vat_result,
                    "ai_used": ai.ai_available(), "cached": True,
                    "updated_at": hit.get("updated_at", "")}

    if not ai.ai_available():
        # 无 Key：返回降级建议，不写缓存（避免模板被当成 AI 结果复用）
        text = ai.generate_tax_advice(data.quarterly_revenue, vat_result, "")
        return {"advice": text, "vat_result": vat_result, "ai_used": False,
                "cached": False}

    prev = db.get_domain_context("tax", cache_key)
    prev_text = prev["value"] if prev else ""
    text = ai.generate_tax_advice(data.quarterly_revenue, vat_result, prev_text)
    db.set_domain_context("tax", cache_key, text)
    return {"advice": text, "vat_result": vat_result, "ai_used": True,
            "cached": False}