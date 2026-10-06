import requests


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token):
        self._base_url = f"https://api.telegram.org/bot{token}"

    def send_message(self, chat_id, text, keyboard=None):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return self._call("sendMessage", payload)

    def edit_message(self, chat_id, message_id, text, keyboard=None):
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return self._call("editMessageText", payload)

    def get_updates(self, offset, timeout):
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        # Telegram holds the request open for `timeout` seconds, so the HTTP timeout must outlast it.
        return self._call("getUpdates", payload, http_timeout=timeout + 10)

    def get_webhook_info(self):
        return self._call("getWebhookInfo", {})

    def _call(self, method, payload, http_timeout=10):
        resp = requests.post(f"{self._base_url}/{method}", json=payload, timeout=http_timeout)
        is_json = resp.headers.get("Content-Type", "").startswith("application/json")
        body = resp.json() if is_json else {}
        if not body.get("ok"):
            detail = body.get("description") or resp.text[:200]
            raise TelegramError(f"{method} failed with HTTP {resp.status_code}: {detail}")
        return body["result"]
