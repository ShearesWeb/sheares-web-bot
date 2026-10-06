from core.context import Context


class Router:
    """Maps Telegram commands, callback_data prefixes and scheduled job names to handler functions."""

    def __init__(self):
        self._commands = {}
        self._callbacks = {}
        self._jobs = {}

    def command(self, name):
        return _registrar(self._commands, name)

    def callback(self, prefix):
        return _registrar(self._callbacks, prefix)

    def job(self, name):
        return _registrar(self._jobs, name)

    def dispatch_update(self, update, services):
        if "message" in update:
            self._dispatch_message(update["message"], services)
        elif "callback_query" in update:
            self._dispatch_callback(update["callback_query"], services)

    def dispatch_job(self, name, payload, telegram):
        if name not in self._jobs:
            raise KeyError(f"No job registered as {name!r}")
        self._jobs[name](payload, telegram)

    def _dispatch_message(self, message, services):
        sender = message.get("from")
        # Anonymous/chat senders do not expose a real user identity.
        if not sender or message.get("sender_chat"):
            return
        text = message.get("text", "")
        handler = self._commands.get(_command_name(text))
        if handler:
            handler(Context(
                user_id=sender["id"], chat_id=message["chat"]["id"], text=text, data="", message_id=None,
                telegram=services.telegram, scheduler=services.scheduler,
            ))

    def _dispatch_callback(self, callback, services):
        if not callback.get("message") or "data" not in callback:
            return
        data = callback["data"]
        matches = [prefix for prefix in self._callbacks if data.startswith(prefix)]
        if not matches:
            return
        message = callback["message"]
        # The button clicker is callback.from, not the bot that sent the message.
        self._callbacks[max(matches, key=len)](Context(
            user_id=callback["from"]["id"], chat_id=message["chat"]["id"], text="", data=data,
            message_id=message["message_id"], telegram=services.telegram, scheduler=services.scheduler,
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


router = Router()
