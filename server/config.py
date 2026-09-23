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
