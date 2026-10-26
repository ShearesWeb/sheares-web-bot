from core import db


class FakeTelegram:
    """Records outgoing Telegram calls so tests assert on what the user would see."""

    def __init__(self):
        self.sent = []
        self.edited = []
        self.files = {}
        self.downloaded = []
        self.callback_answers = []
        self.chats = {}
        self.chat_members = {}

    def send_message(self, chat_id, text, keyboard=None, *, parse_mode="Markdown", reply_markup=None):
        message = {"chat_id": chat_id, "text": text, "keyboard": keyboard}
        if parse_mode != "Markdown":
            message["parse_mode"] = parse_mode
        if reply_markup:
            message["reply_markup"] = reply_markup
        self.sent.append(message)
        return {**message, "message_id": len(self.sent)}

    def get_me(self):
        return {"id": 999, "is_bot": True, "username": "ShearesTestBot"}

    def get_chat(self, chat_id):
        return self.chats[chat_id]

    def get_chat_member(self, chat_id, user_id):
        return self.chat_members[(chat_id, user_id)]

    def download_file(self, file_id, *, max_bytes):
        self.downloaded.append({"file_id": file_id, "max_bytes": max_bytes})
        return self.files[file_id]

    def edit_message(self, chat_id, message_id, text, keyboard=None, *, parse_mode="Markdown"):
        message = {"chat_id": chat_id, "message_id": message_id, "text": text, "keyboard": keyboard}
        if parse_mode != "Markdown":
            message["parse_mode"] = parse_mode
        self.edited.append(message)

    def answer_callback_query(self, callback_id, text=None):
        self.callback_answers.append({"callback_id": callback_id, "text": text})


class RecordingScheduler:
    def __init__(self):
        self.scheduled = []
        self.cancelled = []

    def schedule(self, name, run_at, job, payload):
        self.scheduled.append({"name": name, "run_at": run_at, "job": job, "payload": payload})

    def schedule_recurring(self, name, interval_seconds, job, payload, *, start_at, end_at=None):
        self.scheduled.append({
            "name": name, "interval_seconds": interval_seconds, "job": job,
            "payload": payload, "start_at": start_at, "end_at": end_at,
        })

    def cancel(self, name):
        self.cancelled.append(name)


def message_update(text, user_id=1, chat_id=10, update_id=1, username=None):
    update = {
        "update_id": update_id,
        "message": {"message_id": 5, "from": {"id": user_id}, "chat": {"id": chat_id}, "text": text},
    }
    if username:
        update["message"]["from"]["username"] = username
    return update


def callback_update(data, user_id=1, chat_id=10, message_id=99):
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb",
            "from": {"id": user_id},
            "data": data,
            "message": {"message_id": message_id, "from": {"id": 999, "is_bot": True}, "chat": {"id": chat_id}},
        },
    }


def photo_update(file_id="receipt", user_id=1, chat_id=10, file_size=100, update_id=1):
    update = message_update("", user_id=user_id, chat_id=chat_id, update_id=update_id)
    del update["message"]["text"]
    update["message"]["photo"] = [
        {"file_id": file_id, "width": 960, "height": 1280, "file_size": file_size},
        {"file_id": "thumbnail", "width": 90, "height": 120, "file_size": 10},
    ]
    return update


def document_update(file_id="receipt", mime_type="image/png", user_id=1, chat_id=10, file_size=100, update_id=1):
    update = message_update("", user_id=user_id, chat_id=chat_id, update_id=update_id)
    del update["message"]["text"]
    update["message"]["document"] = {"file_id": file_id, "mime_type": mime_type, "file_size": file_size}
    return update


def users_shared_update(users, request_id, user_id=1, chat_id=10, update_id=20):
    update = message_update("", user_id=user_id, chat_id=chat_id, update_id=update_id)
    del update["message"]["text"]
    update["message"]["users_shared"] = {"request_id": request_id, "users": users}
    return update


def seed_profile(user_id, block, level=3, year=2):
    db.users_table().put_item(Item={
        "userID": f"USER#{user_id}", "profile": "PROFILE", "chatID": str(user_id * 10),
        "block": block, "level": level, "year": year,
    })


def seed_machine(block, machine_id, **attrs):
    db.laundry_table().put_item(Item={"block": block, "machineID": machine_id, **attrs})
