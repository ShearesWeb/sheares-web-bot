import features  # noqa: F401  (registers handlers)
from core import db
from core.router import router
from support import callback_update, message_update, seed_profile


def test_start_and_help_send_welcome(services):
    router.dispatch_update(message_update("/start"), services)
    router.dispatch_update(message_update("/help"), services)

    texts = [m["text"] for m in services.telegram.sent]
    assert len(texts) == 2
    assert all("🤖🧡💙 *Welcome to Sheares Bot!*" in t and "• /dry - Track your drying progress" in t for t in texts)


def test_profile_without_record_offers_creation(services):
    router.dispatch_update(message_update("/profile"), services)

    assert services.telegram.sent == [{
        "chat_id": 10,
        "text": "You don't have a profile yet. Would you like to create one?",
        "keyboard": [[
            {"text": "Yes", "callback_data": "profile_create_yes"},
            {"text": "No", "callback_data": "profile_create_no"},
        ]],
    }]


def test_profile_shows_existing_record(services):
    seed_profile(1, "B", level=3, year=2)

    router.dispatch_update(message_update("/profile"), services)

    (msg,) = services.telegram.sent
    assert msg["text"].startswith("👤 *Your Profile*\nBlock: *B3*\nYear: *2*\n")


def test_profile_creation_flow_saves_record(services):
    for data in ("profile_create_yes", "profile_blk_B", "profile_lvl_B_3", "profile_yr_B_3_2"):
        router.dispatch_update(callback_update(data), services)

    edits = services.telegram.edited
    assert edits[0]["text"] == "Select your Block:"
    assert edits[0]["keyboard"][1][0] == {"text": "D", "callback_data": "profile_blk_D"}
    assert edits[1]["text"] == "Block B selected.\nNow select your Level:"
    assert edits[1]["keyboard"][1][3] == {"text": "8", "callback_data": "profile_lvl_B_8"}
    assert edits[2]["text"] == "Block B, Level 3 selected.\nFinally, select your Year of Study:"
    assert len(edits[2]["keyboard"][0]) == 6
    assert edits[3]["text"] == "✅ Profile created successfully!\nBlock: B\nLevel: 3\nYear: 2"
    item = db.users_table().get_item(Key={"userID": "USER#1", "profile": "PROFILE"})["Item"]
    assert item == {"userID": "USER#1", "profile": "PROFILE", "chatID": "10", "block": "B", "level": 3, "year": 2}


def test_declining_creation_cancels(services):
    router.dispatch_update(callback_update("profile_create_no"), services)

    assert services.telegram.edited[0]["text"] == "Profile creation cancelled."
