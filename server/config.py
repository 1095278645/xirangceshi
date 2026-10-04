# ========== 巷子里的AI掌柜 · 后端配置 ==========
"""配置加载：环境变量 > config.local.json > 默认值。
load_settings() 每次调用实时读取，小程序「设置」页保存后立即生效，无需重启后端。
"""
import json
import os
from pathlib import Path

from safe_io import atomic_write_json

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
_LOCAL_CONFIG = BASE_DIR / "config.local.json"

# 默认值（环境变量优先）
DEFAULT_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEFAULT_LANGUAGE = os.environ.get("SHOP_LANGUAGE", "普通话")
DEFAULT_AI_PIPELINE = os.environ.get("AI_PIPELINE", "fast")
DEFAULT_API_PROFILE = os.environ.get("SHOP_API_PROFILE", "core")

# ---- 业务阈值（从业务代码抽离，统一在此调整，避免散落魔法数）----
YEAR_MIN = 1900          # 期间解析可接受的年份下界
YEAR_MAX = 9999          # 期间解析可接受的年份上界
HTTP_SUCCESS_MAX = 299   # HTTP 成功状态上限（≤ 视为成功，> 视为失败）
HTTP_OK = 200            # 常规成功状态码

# ---- 自适应进化层（批次 B：收敛为"内部离线机制"）----
# 默认关闭：演示/小样本下，进化既不收敛也无法验证；接通真值并建立评测后再打开。
# 打开方式：环境变量 SHOP_ENABLE_EVOLUTION=1，或 config.local.json 里 "enable_evolution": true
ENABLE_EVOLUTION_DEFAULT = os.environ.get("SHOP_ENABLE_EVOLUTION", "0") not in ("0", "false", "False")
# 最小样本量：低于此值时"只记录、不调权、不抑制"，避免小样本误判
EVOLUTION_MIN_SAMPLES = int(os.environ.get("SHOP_EVOLUTION_MIN_SAMPLES", "20") or "20")
# 技能蒸馏
EVOLUTION_DISTILL_SUCCESS_COUNT = int(os.environ.get("SHOP_EVOLUTION_DISTILL_SUCCESS", "7") or "7")
EVOLUTION_DISTILL_HOURS_GAP = int(os.environ.get("SHOP_EVOLUTION_DISTILL_HOURS", "24") or "24")
# 经验晋升
EVOLUTION_PROMOTE_RECURRENCE = int(os.environ.get("SHOP_EVOLUTION_PROMOTE_RECURRENCE", "3") or "3")
EVOLUTION_PROMOTE_DISTINCT_TASKS = int(os.environ.get("SHOP_EVOLUTION_PROMOTE_TASKS", "2") or "2")
EVOLUTION_PROMOTE_DAYS_WINDOW = int(os.environ.get("SHOP_EVOLUTION_PROMOTE_DAYS", "30") or "30")
# 基因抑制
EVOLUTION_SUPPRESS_MIN_ATTEMPTS = int(os.environ.get("SHOP_EVOLUTION_SUPPRESS_MIN", "4") or "4")
EVOLUTION_SUPPRESS_MAX_SUCCESS_RATE = float(
    os.environ.get("SHOP_EVOLUTION_SUPPRESS_RATE", "0.15") or "0.15")
EVOLUTION_SUPPRESS_CONSECUTIVE_INERT = int(
    os.environ.get("SHOP_EVOLUTION_SUPPRESS_INERT", "8") or "8")
# 数据保留（防止 capsules/events/trajectories 无界增长）
EVOLUTION_KEEP_DAYS = int(os.environ.get("SHOP_EVOLUTION_KEEP_DAYS", "90") or "90")
EVOLUTION_KEEP_PER_DOMAIN = int(os.environ.get("SHOP_EVOLUTION_KEEP_ROWS", "500") or "500")
# 验证门（批次 B+：候选基因必须"过门"才能转正）
EVOLUTION_VERIFY_MIN_ADOPTED = int(os.environ.get("SHOP_EVOLUTION_VERIFY_ADOPTED", "3") or "3")
EVOLUTION_VERIFY_MIN_TASKS = int(os.environ.get("SHOP_EVOLUTION_VERIFY_TASKS", "2") or "2")
# 可选外部基准命令（如 `python scripts/eval_ai_parse.py --compare`）；为空则只用证据门。
# 对应 DGM/Hermes 的 "benchmark gate"：退出码 0 才算通过。
EVOLUTION_VERIFY_CMD = os.environ.get("SHOP_EVOLUTION_VERIFY_CMD", "")
EVOLUTION_VERIFY_TIMEOUT = int(os.environ.get("SHOP_EVOLUTION_VERIFY_TIMEOUT", "300") or "300")

# ---- 知识资产治理层（参考图「管得住」：知识要在运行期回到真值上核验）----
# 背景：掌柜的结论（"今天流水 580 元"）写下来就是**资产**，但资产会过期。
# 这里把"陈旧的容忍度"从代码里抽出来：稳定事实必须逐字一致，动态事实允许漂移，
# 但超过容忍度就必须标记为"待复核"，不许继续当结论用（`volatile` 默认 0.20 是刻意宽松的：
# 日流水这类事实每天本来就会变，这里的容忍度管的是"同一口径下的量级失真"，
# 而不是"日期翻篇"——翻篇靠 verified_at 的时间戳判断）。
KNOWLEDGE_VERIFY_DRIFT_STABLE = float(
    os.environ.get("SHOP_KB_DRIFT_STABLE", "0.0") or "0.0")
KNOWLEDGE_VERIFY_DRIFT_SLOW = float(
    os.environ.get("SHOP_KB_DRIFT_SLOW", "0.05") or "0.05")
KNOWLEDGE_VERIFY_DRIFT_VOLATILE = float(
    os.environ.get("SHOP_KB_DRIFT_VOLATILE", "0.20") or "0.20")

# ---- 跨域关系索引层（参考图「看得懂」：关系要能增量合并、也要能回收）----
# stale：多久没再被原始单据证实，就认为这条边可能已经失效（体检出 stale_edges）
# keep：已失效的边保留多久用于回溯（软删在前，物理清理在后）
# scan：一次抽取最多扫多少笔流水（3000+ 笔的演示库不能全量重算，否则接口变慢）
KNOWLEDGE_EDGE_STALE_DAYS = int(
    os.environ.get("SHOP_KB_EDGE_STALE_DAYS", "30") or "30")
KNOWLEDGE_EDGE_KEEP_DAYS = int(
    os.environ.get("SHOP_KB_EDGE_KEEP_DAYS", "90") or "90")
KNOWLEDGE_EDGE_SCAN_LIMIT = int(
    os.environ.get("SHOP_KB_EDGE_SCAN_LIMIT", "3000") or "3000")
# 知识包导出文件的保留期（天）：导出每次都带时间戳，不清理会一直堆在 data/knowledge/
KNOWLEDGE_BUNDLE_KEEP_DAYS = int(
    os.environ.get("SHOP_KB_BUNDLE_KEEP_DAYS", "30") or "30")

# ---- 文案打法（渠道字数上限 + 具体度门槛）----
# 上限口径：一个渠道一个上限，超了 WARN、超 10% 以上 FAIL（见 copy_review._check_channel_length）。
COPY_MOMENTS_MAX_CHARS = int(os.environ.get("SHOP_COPY_MOMENTS_MAX", "220") or "220")
COPY_XHS_BODY_MAX_CHARS = int(os.environ.get("SHOP_COPY_XHS_MAX", "1000") or "1000")
COPY_DOUYIN_MAX_CHARS = int(os.environ.get("SHOP_COPY_DOUYIN_MAX", "200") or "200")
COPY_GROUP_MAX_CHARS = int(os.environ.get("SHOP_COPY_GROUP_MAX", "180") or "180")
COPY_SIGNBOARD_MAX_CHARS = int(os.environ.get("SHOP_COPY_SIGNBOARD_MAX", "20") or "20")
COPY_GROUPBUY_MAX_CHARS = int(os.environ.get("SHOP_COPY_GROUPBUY_MAX", "300") or "300")
COPY_REPLY_MAX_CHARS = int(os.environ.get("SHOP_COPY_REPLY_MAX", "160") or "160")
# 具体度门槛：具体物个数 ÷ 句子数，低于它就判定"像广告不像人话"
COPY_MIN_CONCRETE_RATIO = float(
    os.environ.get("SHOP_COPY_MIN_CONCRETE", "0.5") or "0.5")

# ---- 提示词预算（L11 速度硬约束）----# 进入模型前，提示词过长先告警；超过上限则截断（保留 system，其余截尾）。
# 目的：任何"脚本原始大输出"都不应未经摘要直接喂给模型。
AI_PROMPT_WARN_CHARS = int(os.environ.get("SHOP_AI_PROMPT_WARN", "8000") or "8000")
AI_PROMPT_MAX_CHARS = int(os.environ.get("SHOP_AI_PROMPT_MAX", "24000") or "24000")

# ---- OPC：人力替代与定价（**参考估算值**，各地差异大；可在 config.local.json 覆盖）----
# roi_labor: [{"role": "代账会计", "monthly_yuan": 500, "replaced_ratio": 0.6}, ...]
# roi_price: 建议月订阅价（元）
ROI_LABOR = [
    {"role": "代账会计", "monthly_yuan": 500, "replaced_ratio": 0.6},
    {"role": "朋友圈文案", "monthly_yuan": 300, "replaced_ratio": 0.8},
    {"role": "熟客维系/客服", "monthly_yuan": 1500, "replaced_ratio": 0.3},
    {"role": "经营参谋/店长助理", "monthly_yuan": 4000, "replaced_ratio": 0.2},
]
ROI_SUGGESTED_PRICE_YUAN = float(os.environ.get("SHOP_ROI_PRICE", "99") or "99")
ROI_NOTE = ("人力市场价为**参考区间**（地区/城市差异大），替代比例为保守估计；"
            "AI 成本按本机实际用量计算。可在 config.local.json 用 roi_labor / roi_price 覆盖。")


def evolution_enabled() -> bool:
    """是否启用进化层：环境变量 > config.local.json > 默认（关闭）。"""
    env = os.environ.get("SHOP_ENABLE_EVOLUTION")
    if env not in (None, ""):
        return env not in ("0", "false", "False")
    try:
        if _LOCAL_CONFIG.exists():
            with open(_LOCAL_CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
            if "enable_evolution" in cfg:
                return bool(cfg.get("enable_evolution"))
    except (json.JSONDecodeError, OSError):
        pass
    return ENABLE_EVOLUTION_DEFAULT


def load_settings() -> dict:
    """读取 AI 配置：环境变量 > config.local.json > 默认值。
    返回 {api_key, base_url, model, language}。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = DEFAULT_BASE_URL
    model = DEFAULT_MODEL
    language = DEFAULT_LANGUAGE
    ai_pipeline = DEFAULT_AI_PIPELINE
    api_profile = DEFAULT_API_PROFILE
    if _LOCAL_CONFIG.exists():
        try:
            with open(_LOCAL_CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
            if not api_key:
                api_key = cfg.get("api_key", "")
                base_url = cfg.get("base_url", base_url)
                model = cfg.get("model", model)
            language = cfg.get("language", language)
            ai_pipeline = cfg.get("ai_pipeline", ai_pipeline)
            api_profile = cfg.get("api_profile", api_profile)
        except (json.JSONDecodeError, OSError):
            pass
    return {"api_key": api_key, "base_url": base_url, "model": model,
            "language": language or DEFAULT_LANGUAGE,
            "ai_pipeline": ai_pipeline if ai_pipeline in ("fast", "team") else "fast",
            "api_profile": api_profile if api_profile in ("core", "full") else "core"}


def save_settings(api_key: str | None = None, base_url: str | None = None,
                  model: str | None = None, language: str | None = None,
                  ai_pipeline: str | None = None, api_profile: str | None = None) -> dict:
    """保存配置到 config.local.json（该文件已被 gitignore）。api_key 传 None 表示保留，空串表示清除。
    language 传 None 表示不改动现有口语/方言偏好。返回保存后的完整配置。"""
    cur = load_settings()
    if base_url:
        cur["base_url"] = base_url
    if model:
        cur["model"] = model
    if language is not None:
        cur["language"] = language
    if ai_pipeline in ("fast", "team"):
        cur["ai_pipeline"] = ai_pipeline
    if api_profile in ("core", "full"):
        cur["api_profile"] = api_profile
    if api_key is not None:
        cur["api_key"] = api_key
    # Pattern 21: Atomic Write — 先写 .tmp 再 os.replace，防止写到一半崩溃导致配置损坏
    atomic_write_json(_LOCAL_CONFIG, cur, indent=2)
    return cur


DB_PATH = DATA_DIR / "ai_shopkeeper.db"

# 支持的大模型提供商（均为 OpenAI 兼容接口）
PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek（深度求索）", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "key_label": "API Key（sk- 开头）", "key_url": "https://platform.deepseek.com/api_keys"},
    {"id": "openai", "name": "OpenAI（GPT）", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "key_label": "API Key（sk- 开头）", "key_url": "https://platform.openai.com/api-keys"},
    {"id": "qwen", "name": "通义千问（阿里）", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-turbo", "key_label": "API Key（sk- 开头）", "key_url": "https://bailian.console.aliyun.com/"},
    {"id": "zhipu", "name": "智谱AI（GLM）", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash", "key_label": "API Key", "key_url": "https://open.bigmodel.cn/usercenter/apikeys"},
    {"id": "moonshot", "name": "月之暗面（Kimi）", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "key_label": "API Key（sk- 开头）", "key_url": "https://platform.moonshot.cn/console/api-keys"},
    {"id": "custom", "name": "自定义", "base_url": "", "model": "", "key_label": "API Key", "key_url": ""},
]


def detect_provider(base_url: str) -> str:
    """根据 base_url 反推当前提供商 id（用于前端回显选中项）"""
    for p in PROVIDERS:
        if p["id"] != "custom" and p["base_url"] and p["base_url"] in (base_url or ""):
            return p["id"]
    return "custom"
