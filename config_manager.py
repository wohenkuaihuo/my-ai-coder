import json
import os
import hashlib
from copy import deepcopy
from typing import Any, Dict, List

from llama_index.embeddings.openai import OpenAIEmbedding
from openai import OpenAI


APP_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.join(APP_DIR, ".ai_coder_workspace")
CONFIG_PATH = os.path.join(WORKSPACE_ROOT, "config.json")
ENV_PATH = os.path.join(APP_DIR, ".env")

CHAT_PROFILE_TYPE = "chat"
EMBEDDING_PROFILE_TYPE = "embedding"


class ConfigError(ValueError):
    """Raised when model configuration is missing or invalid."""


def get_project_workspace_dir(target_path: str) -> str:
    """Return the local workspace directory for a mounted target project."""
    project_id = hashlib.md5(os.path.abspath(target_path).encode()).hexdigest()
    workspace_dir = os.path.join(WORKSPACE_ROOT, project_id)
    os.makedirs(workspace_dir, exist_ok=True)
    return workspace_dir


def _read_env_file() -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not os.path.exists(ENV_PATH):
        return values

    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    return values


def _env_value(env_values: Dict[str, str], key: str, default: str = "") -> str:
    return os.environ.get(key) or env_values.get(key, default)


def _default_config() -> Dict[str, Any]:
    env_values = _read_env_file()
    chat_profile = {
        "name": "default-chat",
        "api_key": _env_value(env_values, "AI_CODER_DEFAULT_CHAT_API_KEY"),
        "base_url": _env_value(env_values, "AI_CODER_DEFAULT_CHAT_BASE_URL"),
        "model": _env_value(env_values, "AI_CODER_DEFAULT_CHAT_MODEL"),
    }
    embedding_profile = {
        "name": "default-embedding",
        "api_key": _env_value(env_values, "AI_CODER_DEFAULT_EMBEDDING_API_KEY"),
        "base_url": _env_value(env_values, "AI_CODER_DEFAULT_EMBEDDING_BASE_URL"),
        "model": _env_value(
            env_values,
            "AI_CODER_DEFAULT_EMBEDDING_MODEL",
            "text-embedding-3-small",
        ),
    }
    return {
        "active_chat_profile": chat_profile["name"],
        "active_embedding_profile": embedding_profile["name"],
        "chat_profiles": [chat_profile],
        "embedding_profiles": [embedding_profile],
    }


def _profile_list_key(profile_type: str) -> str:
    if profile_type == CHAT_PROFILE_TYPE:
        return "chat_profiles"
    if profile_type == EMBEDDING_PROFILE_TYPE:
        return "embedding_profiles"
    raise ConfigError(f"未知配置类型: {profile_type}")


def _active_key(profile_type: str) -> str:
    if profile_type == CHAT_PROFILE_TYPE:
        return "active_chat_profile"
    if profile_type == EMBEDDING_PROFILE_TYPE:
        return "active_embedding_profile"
    raise ConfigError(f"未知配置类型: {profile_type}")


def _normalize_profile(profile: Dict[str, Any], fallback_name: str) -> Dict[str, str]:
    return {
        "name": str(profile.get("name") or fallback_name).strip(),
        "api_key": str(profile.get("api_key") or "").strip(),
        "base_url": str(profile.get("base_url") or "").strip(),
        "model": str(profile.get("model") or "").strip(),
    }


def _normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    defaults = _default_config()
    normalized = deepcopy(defaults)
    normalized.update({k: v for k, v in config.items() if k in normalized})

    for profile_type in (CHAT_PROFILE_TYPE, EMBEDDING_PROFILE_TYPE):
        list_key = _profile_list_key(profile_type)
        profiles = normalized.get(list_key)
        if not isinstance(profiles, list):
            profiles = []

        fallback = defaults[list_key][0]
        normalized_profiles: List[Dict[str, str]] = []
        seen_names = set()
        for idx, profile in enumerate(profiles):
            if not isinstance(profile, dict):
                continue
            normalized_profile = _normalize_profile(profile, fallback["name"])
            if not normalized_profile["name"] or normalized_profile["name"] in seen_names:
                normalized_profile["name"] = f"{fallback['name']}-{idx + 1}"
            seen_names.add(normalized_profile["name"])
            normalized_profiles.append(normalized_profile)

        if not normalized_profiles:
            normalized_profiles = [fallback]

        active_key = _active_key(profile_type)
        active_name = str(normalized.get(active_key) or "").strip()
        if active_name not in {p["name"] for p in normalized_profiles}:
            active_name = normalized_profiles[0]["name"]

        normalized[list_key] = normalized_profiles
        normalized[active_key] = active_name

    # If config.json already exists but is empty, keep .env as the default source.
    for profile_type in (CHAT_PROFILE_TYPE, EMBEDDING_PROFILE_TYPE):
        list_key = _profile_list_key(profile_type)
        default_profile = defaults[list_key][0]
        for profile in normalized[list_key]:
            if profile["name"] != default_profile["name"]:
                continue
            for field in ("api_key", "base_url", "model"):
                if not profile.get(field):
                    profile[field] = default_profile.get(field, "")

    return normalized


def load_app_config() -> Dict[str, Any]:
    os.makedirs(WORKSPACE_ROOT, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        config = _default_config()
        save_app_config(config)
        return config

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        config = _default_config()
    return _normalize_config(config)


def save_app_config(config: Dict[str, Any]) -> Dict[str, Any]:
    os.makedirs(WORKSPACE_ROOT, exist_ok=True)
    normalized = _normalize_config(config)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    return normalized


def get_profiles(config: Dict[str, Any], profile_type: str) -> List[Dict[str, str]]:
    return list(config.get(_profile_list_key(profile_type), []))


def get_active_profile(config: Dict[str, Any], profile_type: str) -> Dict[str, str]:
    active_name = config.get(_active_key(profile_type))
    for profile in get_profiles(config, profile_type):
        if profile.get("name") == active_name:
            return profile
    profiles = get_profiles(config, profile_type)
    if profiles:
        return profiles[0]
    raise ConfigError("未找到可用模型配置")


def get_active_chat_config(config: Dict[str, Any] | None = None) -> Dict[str, str]:
    return get_active_profile(config or load_app_config(), CHAT_PROFILE_TYPE)


def get_active_embedding_config(config: Dict[str, Any] | None = None) -> Dict[str, str]:
    return get_active_profile(config or load_app_config(), EMBEDDING_PROFILE_TYPE)


def validate_model_config(profile: Dict[str, str], label: str = "模型") -> None:
    missing_fields = [
        field
        for field in ("api_key", "base_url", "model")
        if not str(profile.get(field) or "").strip()
    ]
    if missing_fields:
        missing_text = "、".join(missing_fields)
        raise ConfigError(f"{label}配置不完整，请先设置: {missing_text}")


def create_openai_client(config: Dict[str, Any] | None = None) -> OpenAI:
    profile = get_active_chat_config(config)
    validate_model_config(profile, "Chat 模型")
    return OpenAI(api_key=profile["api_key"], base_url=profile["base_url"])


def get_active_chat_model(config: Dict[str, Any] | None = None) -> str:
    profile = get_active_chat_config(config)
    validate_model_config(profile, "Chat 模型")
    return profile["model"]


def create_embedding_model(config: Dict[str, Any] | None = None) -> OpenAIEmbedding:
    profile = get_active_embedding_config(config)
    validate_model_config(profile, "Embedding 模型")
    return OpenAIEmbedding(
        model=profile["model"],
        api_key=profile["api_key"],
        api_base=profile["base_url"],
    )
