# ========== 巷子里的AI掌柜 · 后端配置 ==========
"""配置加载：环境变量 > config.local.json > 默认值。
load_settings() 每次调用实时读取，小程序「设置」页保存后立即生效，无需重启后端。
"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
_LOCAL_CONFIG = BASE_DIR / "config.local.json"

# 默认值（环境变量优先）
DEFAULT_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


def load_settings() -> dict:
    """读取 AI 配置：环境变量 > config.local.json > 默认值。返回 {api_key, base_url, model}。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = DEFAULT_BASE_URL
    model = DEFAULT_MODEL
    if not api_key and _LOCAL_CONFIG.exists():
        try:
            with open(_LOCAL_CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
            api_key = cfg.get("api_key", "")
            base_url = cfg.get("base_url", base_url)
            model = cfg.get("model", model)
        except (json.JSONDecodeError, OSError):
            pass
    return {"api_key": api_key, "base_url": base_url, "model": model}


def save_settings(api_key: str = "", base_url: str | None = None, model: str | None = None) -> dict:
    """保存配置到 config.local.json（该文件已被 gitignore）。api_key 传空串表示清除。
    返回保存后的完整配置。"""
    cur = load_settings()
    if base_url:
        cur["base_url"] = base_url
    if model:
        cur["model"] = model
    cur["api_key"] = api_key
    with open(_LOCAL_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    return cur


DB_PATH = DATA_DIR / "ai_shopkeeper.db"