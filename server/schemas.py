"""巷子里的AI掌柜 · API 请求模型（Pydantic）

从 main.py 抽出：单一职责（L2）+ 渐进披露（L3），路由层只关心业务。
所有模型均带默认值，前端不传即用业务兜底。
"""
from pydantic import BaseModel


class OrderIn(BaseModel):
    text: str                       # 语音转写或手动输入的记账文本
    customer: str = ""              # 可选：手工指定客户
    amount: float | None = None


class MemoryIn(BaseModel):
    customer_id: int
    content: str


class CustomerIn(BaseModel):
    name: str
    phone: str = ""
    tags: str = ""
    favorite: str = ""


class CopyIn(BaseModel):
    shop_name: str = "我的小店"
    scene: str = "今日营业"
    extra: str = ""
    customer_name: str = ""


class SettingsIn(BaseModel):
    api_key: str = ""          # 传空串 = 清除 Key
    base_url: str = ""
    model: str = ""


class VatIn(BaseModel):
    quarterly_revenue: float   # 季度销售额


class SurtaxIn(BaseModel):
    vat: float                 # 实缴增值税
    is_small: bool = True      # 是否小规模纳税人


class PitIn(BaseModel):
    salary: float              # 月工资
    social_insurance: float = 0
    special_deduction: float = 0


class CitIn(BaseModel):
    annual_income: float       # 年应纳税所得额
    is_small: bool = True      # 是否小微企业


class PaymentSourceIn(BaseModel):
    """收款账户（微信商户号 / 聚合支付）"""
    sid: int | None = None     # 有值=更新
    source_type: str = "wechat"   # wechat / aggregate
    name: str = ""
    mchid: str = ""
    appid: str = ""
    cert_path: str = ""
    private_key_path: str = ""
    api_v3_key: str = ""
    enabled: bool = False


class StoreModelIn(BaseModel):
    """单店经营模型输入（多业态泛化）"""
    daily_revenue: float = 0           # 实际日营业额（元）
    gross_margin: float | None = None  # 毛利率（小数）；None 用业态默认
    rent: float = 0                    # 月房租
    salary: float = 0                  # 月人工
    utilities: float = 0               # 月水电杂费
    total_investment: float = 0        # 总投资
    cash_on_hand: float = 0            # 现有现金
    traffic: str = "一般"              # 商圈客流：差/一般/好
    competitor: str = "一般"           # 周边竞争：多/一般/少
    biz_type: str = "餐饮"             # 业态：餐饮/饮品/零售/生鲜/服务/摆摊


class DomainContextIn(BaseModel):
    """领域上下文写入（按业务域独立的经营记忆）"""
    domain: str                        # 如 ledger / customer / copy / tax / stock
    key: str = ""                      # 空串时用 domain 作 key（单值场景）
    value: object = ""                 # 任意 JSON 可序列化值


class JobIn(BaseModel):
    """任务入队"""
    task_type: str
    payload: object = None


class InsightIn(BaseModel):
    """月度经营洞察请求（不传年月默认当月）"""
    year: int | None = None
    month: int | None = None


class CopyContextIn(BaseModel):
    """文案生成上下文（从 domain_context 读取的经营记忆）"""
    shop_name: str = "我的小店"
    scene: str = "今日营业"
    extra: str = ""
    customer_name: str = ""


class StoreProfileIn(BaseModel):
    """单店档案保存（input 直喂 calc_store_model）"""
    name: str = "我的店"
    biz_type: str = "餐饮"
    gross_margin: float | None = None
    rent: float = 0
    salary: float = 0
    utilities: float = 0
    total_investment: float = 0
    cash_on_hand: float = 0
    traffic: str = "一般"
    competitor: str = "一般"


class BudgetIn(BaseModel):
    """月度预算（亲民：每月计划花/进多少）"""
    month: str                       # YYYY-MM
    scope: str = "expense"           # income / expense
    amount: float = 0
    category: str = ""
    note: str = ""
    bid: int | None = None           # 有值=更新


class DebtIn(BaseModel):
    """应收应付（亲民：谁欠我钱/我欠谁钱）"""
    party: str = ""
    kind: str = "receivable"         # receivable 应收 / payable 应付
    amount: float = 0
    due_date: str = ""               # YYYY-MM-DD，到期日
    note: str = ""
    did: int | None = None           # 有值=更新


class SettleDebtIn(BaseModel):
    """结清应收应付"""
    settle_amount: float | None = None   # 默认全额


class ProductIn(BaseModel):
    """商品/原材料（库存进销存）"""
    name: str
    category: str = ""
    unit: str = ""
    stock_qty: float = 0
    safety_stock: float = 0
    unit_cost: float = 0
    expiry_date: str = ""            # YYYY-MM-DD 保质期
    supplier: str = ""
    note: str = ""
    pid: int | None = None           # 有值=更新


class StockMoveIn(BaseModel):
    """库存变动（入库/出库/盘点）"""
    movement: str = "in"             # in 入库 / out 出库 / adj 盘点
    qty: float = 0
    note: str = ""


class InvoiceIn(BaseModel):
    """发票台账"""
    kind: str = "out"                # out 销项开票 / in 进项收票
    party: str = ""
    invoice_no: str = ""
    amount: float = 0
    rate: float = 0
    tax_amount: float = 0
    issued_date: str = ""            # YYYY-MM-DD
    note: str = ""
    iid: int | None = None           # 有值=更新


class CashflowIn(BaseModel):
    """现金流滚动预测入参"""
    cash_on_hand: float = 0
    months: int = 6
    safety_buffer: float = 0         # 月均固定成本（用于「不够花」预警）