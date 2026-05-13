import json
import os
import shutil
from datetime import datetime
from typing import Dict, List


MAX_MEMORY_MESSAGES = 200
MEMORY_FILE_NAME = "chat_memory.json"


def _memory_path(workspace_dir: str) -> str:
    return os.path.join(workspace_dir, MEMORY_FILE_NAME)


def _normalize_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    normalized = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "").strip()
        if role in {"user", "assistant", "system"} and content:
            normalized.append({"role": role, "content": content})
    return normalized[-MAX_MEMORY_MESSAGES:]


def load_chat_memory(workspace_dir: str) -> List[Dict[str, str]]:
    os.makedirs(workspace_dir, exist_ok=True)
    path = _memory_path(workspace_dir)
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return _normalize_messages(data)
    except (OSError, json.JSONDecodeError):
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = f"{path}.{timestamp}.bak"
        try:
            shutil.move(path, backup_path)
        except OSError:
            pass
    return []


def save_chat_memory(workspace_dir: str, messages: List[Dict[str, str]]) -> None:
    os.makedirs(workspace_dir, exist_ok=True)
    path = _memory_path(workspace_dir)
    normalized = _normalize_messages(messages)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)


def append_chat_message(workspace_dir: str, messages: List[Dict[str, str]], role: str, content: str) -> List[Dict[str, str]]:
    updated_messages = _normalize_messages(messages + [{"role": role, "content": content}])
    save_chat_memory(workspace_dir, updated_messages)
    return updated_messages
