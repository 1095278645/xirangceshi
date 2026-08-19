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