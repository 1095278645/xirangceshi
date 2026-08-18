# ========== 巷子里的AI掌柜 · 后端配置 ==========
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ---- DeepSeek API 配置 ----
# 优先读取环境变量；没有则从 config.local.json 读取（该文件已 gitignore，不会上传）
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

_LOCAL_CONFIG = BASE_DIR / "config.local.json"
if not DEEPSEEK_API_KEY and _LOCAL_CONFIG.exists():
    import json
    with open(_LOCAL_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    DEEPSEEK_API_KEY = cfg.get("api_key", "")
    DEEPSEEK_BASE_URL = cfg.get("base_url", DEEPSEEK_BASE_URL)
    DEEPSEEK_MODEL = cfg.get("model", DEEPSEEK_MODEL)

DB_PATH = DATA_DIR / "ai_shopkeeper.db"