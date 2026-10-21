"""下载历史记录：GUI 与网页版共用，持久化在 ~/.streamdl_history.json。"""

import json
import os

HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".streamdl_history.json")


def load_history() -> list[dict]:
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - 历史文件损坏时从零开始
        return []


def save_history(records: list[dict]) -> None:
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(records[-200:], f, ensure_ascii=False, indent=1)
    except OSError:
        pass
