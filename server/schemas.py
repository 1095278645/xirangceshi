"""巷子里的AI掌柜 · API 请求模型（Pydantic）

从 main.py 抽出：单一职责（L2）+ 渐进披露（L3），路由层只关心业务。
所有模型均带默认值，前端不传即用业务兜底。
"""
from pydantic import BaseModel, Field


class OrderIn(BaseModel):
    text: str                       # 语音转写或手动输入的记账文本
    customer: str = ""              # 可选：手工指定客户
    amount: float | None = Field(default=None, ge=0)
    # 补记金额时用：界面在"金额没听懂"之后，把 AI 已经解析好的字段原样带回来
    # （item + amount 都给齐时后端不再调 AI —— 既省一次调用，也保证补记的
    #   科目/熟客与第一次解析完全一致，不会因为重解析而漂移）
    item: str = ""
    category: str = ""
    trans_type: str = ""
    note: str = ""


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


class VoiceIn(BaseModel):
    """语音转写：前端录音（base64，无 data: 前缀）"""
    audio: str = ""
    format: str = "wav"           # 音频格式：wav / mp3


class SettingsIn(BaseModel):
    api_key: str = ""          # 传空串 = 清除 Key
    base_url: str = ""
    model: str = ""
    language: str | None = None   # 口语/方言偏好（普通话/粤语/四川话/英语…），None=不改


class VatIn(BaseModel):
    quarterly_revenue: float = Field(ge=0)   # 季度销售额
    refresh: bool = False                    # 报税建议：True=强制重新生成


class SurtaxIn(BaseModel):
    vat: float = Field(ge=0)   # 实缴增值税
    is_small: bool = True      # 是否小规模纳税人


class PitIn(BaseModel):
    salary: float = Field(ge=0)            # 月工资
    social_insurance: float = Field(default=0, ge=0)
    special_deduction: float = Field(default=0, ge=0)


class CitIn(BaseModel):
    annual_income: float = Field(ge=0)     # 年应纳税所得额
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
    daily_revenue: float = Field(default=0, ge=0)       # 日营业额（元）
    gross_margin: float | None = Field(default=None, ge=0)  # 毛利率（小数）；None 用业态默认
    rent: float = Field(default=0, ge=0)               # 月房租
    salary: float = Field(default=0, ge=0)             # 月人工
    utilities: float = Field(default=0, ge=0)          # 月水电杂费
    total_investment: float = Field(default=0, ge=0)   # 总投资
    cash_on_hand: float = Field(default=0, ge=0)       # 现有现金
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
    refresh: bool = False     # True=强制重新生成；默认命中缓存直接返回


class TransactionEditIn(BaseModel):
    """交易更正（只传需要改的字段）"""
    item: str | None = None
    amount: float | None = Field(default=None, ge=0)
    category: str | None = None
    counterparty: str | None = None
    note: str | None = None
    trans_type: str | None = None
    customer_id: int | None = None
    reason: str = ""          # 更正原因，写入审计


class VoidIn(BaseModel):
    """作废交易"""
    reason: str = ""


class RefundIn(BaseModel):
    """退货冲销：amount 省略表示全额退"""
    amount: float | None = Field(default=None, gt=0)
    reason: str = ""


class ReviewFeedbackIn(BaseModel):
    """店主对掌柜复盘的反馈：有用/没用 + 一句话原因"""
    useful: bool
    reason: str = ""


class CollectionIn(BaseModel):
    """创建收款请求"""
    amount: float = Field(gt=0, le=1_000_000)
    item: str = ""
    customer_id: int | None = None
    note: str = ""
    payer_name: str = ""


class CollectionConfirmIn(BaseModel):
    """顾客侧标记已付款 / 店主侧确认与取消的参数"""
    payer_name: str = ""      # 顾客填写
    item: str | None = None   # 店主确认时可改事由
    category: str | None = None


class NotifySubscriptionIn(BaseModel):
    """新增/更新推送订阅"""
    channel: str
    target: str = ""
    events: list[str] = []
    enabled: bool = True
    name: str = ""
    sid: int | None = None


class NotifyTestIn(BaseModel):
    """直接试发一条消息（不建订阅）"""
    channel: str
    target: str = ""
    title: str = ""
    content: str = ""


class WecomBotIn(BaseModel):
    """一步配置企业微信群机器人"""
    key: str
    events: list[str] = []
    name: str = ""
    sid: int | None = None


class ClosePeriodIn(BaseModel):
    """期末结转 / 反结转"""
    period: str          # YYYY-MM
    note: str = ""


class OpeningBalanceIn(BaseModel):
    """设置科目期初余额（把历史账套接进来时用）"""
    account_code: str
    amount: float = 0
    note: str = ""


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
    gross_margin: float | None = Field(default=None, ge=0)
    rent: float = Field(default=0, ge=0)
    salary: float = Field(default=0, ge=0)
    utilities: float = Field(default=0, ge=0)
    total_investment: float = Field(default=0, ge=0)
    cash_on_hand: float = Field(default=0, ge=0)
    traffic: str = "一般"
    competitor: str = "一般"


class BudgetIn(BaseModel):
    """月度预算（亲民：每月计划花/进多少）"""
    month: str                       # YYYY-MM
    scope: str = "expense"           # income / expense
    amount: float = Field(default=0, ge=0)
    category: str = ""
    note: str = ""
    bid: int | None = None           # 有值=更新


class DebtIn(BaseModel):
    """应收应付（亲民：谁欠我钱/我欠谁钱）"""
    party: str = ""
    kind: str = "receivable"         # receivable 应收 / payable 应付
    amount: float = Field(default=0, ge=0)
    due_date: str = ""               # YYYY-MM-DD，到期日
    note: str = ""
    did: int | None = None           # 有值=更新


class SettleDebtIn(BaseModel):
    """结清应收应付"""
    settle_amount: float | None = Field(default=None, ge=0)   # 默认全额


class ProductIn(BaseModel):
    """商品/原材料（库存进销存）"""
    name: str
    category: str = ""
    unit: str = ""
    stock_qty: float = Field(default=0, ge=0)
    safety_stock: float = Field(default=0, ge=0)
    unit_cost: float = Field(default=0, ge=0)
    expiry_date: str = ""            # YYYY-MM-DD 保质期
    supplier: str = ""
    note: str = ""
    pid: int | None = None           # 有值=更新


class StockMoveIn(BaseModel):
    """库存变动（入库/出库/盘点）"""
    movement: str = "in"             # in 入库 / out 出库 / adj 盘点
    qty: float = Field(ge=0)         # 数量不允许为负
    note: str = ""


class InvoiceIn(BaseModel):
    """发票台账"""
    kind: str = "out"                # out 销项开票 / in 进项收票
    party: str = ""
    invoice_no: str = ""
    amount: float = Field(ge=0)
    rate: float = Field(default=0, ge=0)
    tax_amount: float = Field(default=0, ge=0)
    issued_date: str = ""            # YYYY-MM-DD
    note: str = ""
    iid: int | None = None           # 有值=更新


class CashflowIn(BaseModel):
    """现金流滚动预测入参"""
    cash_on_hand: float = Field(default=0, ge=0)
    months: int = Field(default=6, ge=1)
    safety_buffer: float = Field(default=0, ge=0)   # 月均固定成本（用于「不够花」预警）


# ===== 自适应进化层 =====

class LearningIn(BaseModel):
    """经验日志上报"""
    domain: str = "copy"
    trigger_type: str                # user_edited / repeated_request / all_skipped / degraded / margin_abnormal / explicit_feedback
    pattern_key: str = ""            # domain.symptom 格式去重键
    source: str = "frontend"         # frontend / system / tool_result / user_correction
    details: str = ""
    metadata: dict | None = None


class LearningQuery(BaseModel):
    """经验日志查询"""
    domain: str = ""
    status: str = ""                # open / resolved / promoted


class OutcomeIn(BaseModel):
    """用户行为结果记录（采纳/修改/跳过）"""
    domain: str = "copy"
    gene_id: str = ""
    content: str = ""
    user_adopted: bool = False
    user_edited: bool = False
    edit_diff: str = ""
    task_context: dict | None = None


class GeneIn(BaseModel):
    """基因创建/更新"""
    gene_id: str
    domain: str
    trigger_signals: list = []
    system_prompt_addon: str = ""
    strategy_steps: list | None = None
    category: str = "innovate"      # innovate / repair / reinforce
    is_distilled: int = 0
