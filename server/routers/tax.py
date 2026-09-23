"""税法计算（省账通能力）+ 科目表台账"""
from fastapi import APIRouter

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