import json
from pathlib import Path
from typing import Any

from bot.config import config


class BotContent:
    def __init__(self, path: str):
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RuntimeError(f"Bot content config not found: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Bot content config is invalid JSON: {self.path}") from exc

    def other_animal_label(self) -> str:
        value = self._read().get("other_animal_label")
        if not isinstance(value, str) or not value:
            raise KeyError("Other animal label is not configured")
        return value

    def animal_type_max_length(self) -> int:
        value = self._read().get("animal_type_max_length", 50)
        return value if isinstance(value, int) and value > 0 else 50

    def month_name(self, month: int) -> str:
        data = self._read()
        month_names = data.get("calendar", {}).get("month_names", [])
        if not isinstance(month_names, list) or not 1 <= month <= len(month_names):
            raise KeyError("Calendar month names are not configured")
        value = month_names[month - 1]
        if not isinstance(value, str):
            raise KeyError("Calendar month name is not configured")
        return value

    def weekday_names(self) -> list[str]:
        data = self._read()
        weekday_names = data.get("calendar", {}).get("weekday_names", [])
        if (
            not isinstance(weekday_names, list)
            or len(weekday_names) != 7
            or not all(isinstance(value, str) for value in weekday_names)
        ):
            raise KeyError("Calendar weekday names are not configured")
        return weekday_names.copy()

    def button(self, key: str, **kwargs: Any) -> str:
        data = self._read()
        value = data.get("buttons", {}).get(key)
        if not isinstance(value, str):
            raise KeyError(f"Button text is not configured: {key}")
        return value.format(**kwargs)

    def message(self, key: str, **kwargs: Any) -> str:
        data = self._read()
        value = data.get("messages", {}).get(key)
        if not isinstance(value, str):
            raise KeyError(f"Message is not configured: {key}")
        return value.format(**kwargs)

    def status_label(self, status: Any) -> str:
        status_key = getattr(status, "name", str(status))
        data = self._read()
        value = data.get("post_status_labels", {}).get(status_key)
        return value if isinstance(value, str) else status_key.lower()


bot_content = BotContent(config.BOT_CONTENT_PATH)
