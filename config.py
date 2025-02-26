import json
import os


CONFIG_FILE = "config.json"
default_config = {
    "call_strategy": "random",  # 可选值："random", "high", "low"
    "custom_api_key": "",  # 空字符串表示不使用自定义api_key
}

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        CALL_STRATEGY = cfg.get("call_strategy", default_config["call_strategy"])
        CUSTOM_API_KEY = cfg.get("custom_api_key", default_config["custom_api_key"])
    except Exception:
        CALL_STRATEGY = default_config["call_strategy"]
        CUSTOM_API_KEY = default_config["custom_api_key"]
else:
    CALL_STRATEGY = default_config["call_strategy"]
    CUSTOM_API_KEY = default_config["custom_api_key"]


def save_config():
    global CALL_STRATEGY, CUSTOM_API_KEY
    cfg = {"call_strategy": CALL_STRATEGY, "custom_api_key": CUSTOM_API_KEY}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def update_call_strategy(new_strategy: str):
    global CALL_STRATEGY
    CALL_STRATEGY = new_strategy
    save_config()


def update_custom_api_key(new_key: str):
    global CUSTOM_API_KEY
    CUSTOM_API_KEY = new_key
    save_config()
