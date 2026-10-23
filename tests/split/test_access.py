import pytest

import features  # noqa: F401
from core.router import router
from features.split import repository
from support import callback_update, document_update, message_update, photo_update, seed_profile
from features.profile import repository as profile_repository

PROFILE_PROMPT = "Please create a /profile first."
TOKEN = "a" * 32


@pytest.mark.parametrize("command", ["/split", "/done", "/method", "/cancel", "/bills", "/reminders", "/reminder"])
@pytest.mark.parametrize("chat_id", [10, -100])
def test_bill_commands_require_a_profile(services, command, chat_id):
    router.dispatch_update(message_update(command, chat_id=chat_id), services)

    assert services.telegram.sent == [{
        "chat_id": chat_id, "text": PROFILE_PROMPT, "keyboard": None, "parse_mode": None,
    }]
    assert repository.get_session(1, chat_id) is None


@pytest.mark.parametrize("action", ["split", "bills", "reminders", "reminder"])
def test_private_links_require_a_profile(services, action):
    router.dispatch_update(message_update(f"/start {action}"), services)

    assert services.telegram.sent[-1]["text"] == PROFILE_PROMPT
    assert repository.get_session(1, 10) is None


@pytest.mark.parametrize("data", [
    f"split_method:equal:{TOKEN}", f"split_pick:finish:{TOKEN}", "split_list:overview:0",
    f"split_pay:{TOKEN}", f"split_recv:{TOKEN}:2", f"split_bill:share:{TOKEN}",
    f"split_remind:setup:{TOKEN}", "split_remind:list:0",
])
def test_bill_buttons_require_a_profile_and_acknowledge_the_callback(services, data):
    router.dispatch_update(callback_update(data), services)

    assert services.telegram.sent[-1]["text"] == PROFILE_PROMPT
    assert services.telegram.edited == []
    assert services.telegram.callback_answers == [{"callback_id": "cb", "text": None}]


@pytest.mark.parametrize("upload", [photo_update, document_update])
def test_receipt_uploads_require_a_profile_even_with_an_existing_session(services, textract, upload):
    session = repository.start_session(1, 10)

    router.dispatch_update(upload(), services)

    assert services.telegram.sent[-1]["text"] == PROFILE_PROMPT
    assert services.telegram.downloaded == []
    assert repository.get_session(1, 10) == session


def test_adding_text_to_an_existing_session_requires_a_profile(services):
    session = repository.start_session(1, 10)

    router.dispatch_update(message_update("Lunch - 10"), services)

    assert services.telegram.sent[-1]["text"] == PROFILE_PROMPT
    assert repository.get_session(1, 10) == session


def test_profile_without_a_block_does_not_unlock_bill_commands(services):
    seed_profile(1, "")

    router.dispatch_update(message_update("/split"), services)

    assert services.telegram.sent[-1]["text"] == PROFILE_PROMPT
    assert repository.get_session(1, 10) is None


def test_profile_onboarding_remains_accessible_and_unlocks_bill_splitting(services):
    router.dispatch_update(message_update("/profile"), services)
    assert "Would you like to create one?" in services.telegram.sent[-1]["text"]
    for data in ("profile_create_yes", "profile_blk_A", "profile_lvl_A_3", "profile_yr_A_3_2"):
        router.dispatch_update(callback_update(data), services)

    router.dispatch_update(message_update("/split"), services)

    assert repository.get_session(1, 10)["status"] == "COLLECTING"
    assert "Send expenses as text" in services.telegram.sent[-1]["text"]


@pytest.mark.parametrize("action, expected", [
    ("split", "Send expenses as text"), ("bills", "<b>Your bills</b>"), ("reminders", "<b>Pending reminders</b>"),
])
def test_private_links_open_the_requested_flow_for_profiled_users(owner_profile, services, action, expected):
    router.dispatch_update(message_update(f"/start {action}"), services)

    assert expected in services.telegram.sent[-1]["text"]


def test_ordinary_chat_without_a_split_does_not_prompt_for_a_profile(services):
    router.dispatch_update(message_update("Hello"), services)

    assert services.telegram.sent == []


@pytest.mark.parametrize("upload", [photo_update, document_update])
@pytest.mark.parametrize("has_profile", [False, True])
@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
def test_group_images_are_ignored_before_the_profile_check(services, textract, monkeypatch, upload, has_profile, chat_type):
    if has_profile:
        seed_profile(1, "A")

    def unexpected_profile_read(*args):
        raise AssertionError("A group photo must not enter the profile or split flow")

    monkeypatch.setattr(profile_repository, "get_profile", unexpected_profile_read)
    update = upload(chat_id=-100)
    update["message"]["chat"]["type"] = chat_type
    update["message"]["reply_to_message"] = {"from": {"id": 999, "is_bot": True}, "text": "Payment reminder"}

    router.dispatch_update(update, services)

    assert services.telegram.sent == []
    assert services.telegram.edited == []
    assert services.telegram.downloaded == []
