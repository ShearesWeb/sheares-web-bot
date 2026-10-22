import requests


class TelegramError(Exception):
    def __init__(self, message="Telegram request failed.", *, code=None):
        super().__init__(message)
        self.code = code


class FileTooLargeError(TelegramError):
    pass


class TelegramClient:
    def __init__(self, token):
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._file_base_url = f"https://api.telegram.org/file/bot{token}"

    def send_message(self, chat_id, text, keyboard=None, *, parse_mode="Markdown", reply_markup=None):
        if keyboard and reply_markup:
            raise ValueError("Use either an inline keyboard or reply_markup.")
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        elif reply_markup:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", payload)

    def edit_message(self, chat_id, message_id, text, keyboard=None, *, parse_mode="Markdown"):
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if keyboard is not None:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return self._call("editMessageText", payload)

    def answer_callback_query(self, callback_id, text=None):
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        return self._call("answerCallbackQuery", payload)

    def get_updates(self, offset, timeout):
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        # Telegram holds the request open for `timeout` seconds, so the HTTP timeout must outlast it.
        return self._call("getUpdates", payload, http_timeout=timeout + 10)

    def get_webhook_info(self):
        return self._call("getWebhookInfo", {})

    def get_me(self):
        return self._call("getMe", {})

    def get_chat(self, chat_id):
        return self._call("getChat", {"chat_id": chat_id})

    def get_chat_member(self, chat_id, user_id):
        return self._call("getChatMember", {"chat_id": chat_id, "user_id": user_id})

    def download_file(self, file_id, *, max_bytes):
        try:
            file = self._call("getFile", {"file_id": file_id})
            if file.get("file_size", 0) > max_bytes:
                raise FileTooLargeError("The uploaded file is too large.")
            if not file.get("file_path"):
                raise TelegramError("Telegram did not provide a download path.")
            with requests.get(
                f"{self._file_base_url}/{file['file_path']}", stream=True, timeout=10,
            ) as response:
                if response.status_code != 200:
                    raise TelegramError(f"File download failed with HTTP {response.status_code}.")
                content = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if len(content) + len(chunk) > max_bytes:
                        raise FileTooLargeError("The uploaded file is too large.")
                    content.extend(chunk)
                return bytes(content)
        except requests.RequestException:
            # Request exceptions contain the download URL, including the bot token.
            raise TelegramError("Could not download the uploaded file from Telegram.") from None

    def _call(self, method, payload, http_timeout=10):
        try:
            resp = requests.post(f"{self._base_url}/{method}", json=payload, timeout=http_timeout)
        except requests.RequestException:
            raise TelegramError(f"Could not reach Telegram for {method}.") from None
        is_json = resp.headers.get("Content-Type", "").startswith("application/json")
        body = resp.json() if is_json else {}
        if not body.get("ok"):
            detail = body.get("description") or resp.text[:200]
            raise TelegramError(f"{method} failed with HTTP {resp.status_code}: {detail}", code=body.get("error_code", resp.status_code))
        return body["result"]
