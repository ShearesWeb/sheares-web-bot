from core.context import Attachment, Context
from core.identities import remember_user


class Router:
    """Maps Telegram commands, message types, callback prefixes and jobs to handlers."""

    def __init__(self):
        self._commands = {}
        self._messages = {}
        self._callbacks = {}
        self._jobs = {}
        self._jobs_with_scheduler = set()

    def command(self, name):
        return _registrar(self._commands, name)

    def message(self, kind):
        return _registrar(self._messages, kind)

    def callback(self, prefix):
        return _registrar(self._callbacks, prefix)

    def job(self, name, *, with_scheduler=False):
        def register(fn):
            _registrar(self._jobs, name)(fn)
            if with_scheduler:
                self._jobs_with_scheduler.add(name)
            return fn
        return register

    def dispatch_update(self, update, services):
        if "message" in update:
            self._dispatch_message(update["message"], services, update.get("update_id"))
        elif "callback_query" in update:
            self._dispatch_callback(update["callback_query"], services)

    def dispatch_job(self, name, payload, telegram, *, scheduler=None):
        if name not in self._jobs:
            raise KeyError(f"No job registered as {name!r}")
        if name in self._jobs_with_scheduler:
            if scheduler is None:
                raise ValueError(f"Job {name!r} requires a scheduler.")
            return self._jobs[name](payload, telegram, scheduler)
        return self._jobs[name](payload, telegram)

    def _dispatch_message(self, message, services, update_id=None):
        sender = message.get("from")
        # Anonymous/chat senders do not expose a real user identity.
        if not sender or message.get("sender_chat"):
            return
        if sender.get("is_bot"):
            return
        remember_user(sender)
        text = message.get("text", "")
        handler = self._commands.get(_command_name(text))
        attachment = None
        if not handler:
            kind, attachment = _attachment(message)
            handler = self._messages.get(kind)
        if handler:
            handler(Context(
                user_id=sender["id"], chat_id=message["chat"]["id"], text=text, data="", message_id=None,
                telegram=services.telegram, scheduler=services.scheduler, attachment=attachment,
                update_id=update_id,
                chat_type=message["chat"].get("type", "private" if message["chat"]["id"] > 0 else "group"),
                shared_users=tuple(message.get("users_shared", {}).get("users", [])),
                shared_request_id=message.get("users_shared", {}).get("request_id"),
                chat_title=message["chat"].get("title", ""), shared_chat=message.get("chat_shared"),
            ))

    def _dispatch_callback(self, callback, services):
        if not callback.get("message") or "data" not in callback:
            return
        if not callback.get("from") or callback["from"].get("is_bot"):
            return
        remember_user(callback["from"])
        data = callback["data"]
        matches = [prefix for prefix in self._callbacks if data.startswith(prefix)]
        if not matches:
            return
        message = callback["message"]
        # The button clicker is callback.from, not the bot that sent the message.
        self._callbacks[max(matches, key=len)](Context(
            user_id=callback["from"]["id"], chat_id=message["chat"]["id"], text="", data=data,
            message_id=message["message_id"], telegram=services.telegram, scheduler=services.scheduler,
            callback_id=callback.get("id"),
            chat_type=message["chat"].get("type", "private" if message["chat"]["id"] > 0 else "group"),
            chat_title=message["chat"].get("title", ""),
        ))


def _registrar(table, key):
    def register(fn):
        if key in table:
            raise ValueError(f"{key!r} is already registered to {table[key].__name__}")
        table[key] = fn
        return fn
    return register


def _command_name(text):
    tokens = text.split(maxsplit=1)
    # Group chats send "/wash@ShearesBot"; the suffix names the bot, not the command.
    return tokens[0].split("@", 1)[0] if tokens else ""


def _attachment(message):
    if message.get("chat_shared"):
        return "chat_shared", None
    if message.get("users_shared"):
        return "users_shared", None
    if message.get("photo"):
        # Telegram provides several sizes; OCR needs the highest resolution.
        photo = max(message["photo"], key=lambda p: p["width"] * p["height"])
        return "photo", Attachment(photo["file_id"], "image/jpeg", photo.get("file_size"))
    if message.get("document"):
        document = message["document"]
        return "document", Attachment(
            document["file_id"], document.get("mime_type", ""), document.get("file_size"),
        )
    if message.get("text") and not message["text"].lstrip().startswith("/"):
        return "text", None
    return None, None


router = Router()
