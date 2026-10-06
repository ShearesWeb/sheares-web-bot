from core import db


class FakeTelegram:
    """Records outgoing Telegram calls so tests assert on what the user would see."""

    def __init__(self):
        self.sent = []
        self.edited = []

    def send_message(self, chat_id, text, keyboard=None):
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})

    def edit_message(self, chat_id, message_id, text, keyboard=None):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text, "keyboard": keyboard})


class RecordingScheduler:
    def __init__(self):
        self.scheduled = []

    def schedule(self, name, run_at, job, payload):
        self.scheduled.append({"name": name, "run_at": run_at, "job": job, "payload": payload})


def message_update(text, user_id=1, chat_id=10, update_id=1):
    return {
        "update_id": update_id,
        "message": {"message_id": 5, "from": {"id": user_id}, "chat": {"id": chat_id}, "text": text},
    }


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


def seed_profile(user_id, block, level=3, year=2):
    db.users_table().put_item(Item={
        "userID": f"USER#{user_id}", "profile": "PROFILE", "chatID": str(user_id * 10),
        "block": block, "level": level, "year": year,
    })


def seed_machine(block, machine_id, **attrs):
    db.laundry_table().put_item(Item={"block": block, "machineID": machine_id, **attrs})
