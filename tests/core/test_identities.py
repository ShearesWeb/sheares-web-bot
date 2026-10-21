from core import identities


def test_unknown_and_renamed_handles_do_not_resolve_to_old_identity():
    assert identities.resolve_username("alice") is None
    identities.remember_user({"id": 42, "username": "Alice", "first_name": "Alice"})
    assert identities.resolve_username("@ALICE")["telegramID"] == "42"
    identities.remember_user({"id": 42, "username": "new_alice"})
    assert identities.resolve_username("alice") is None
    assert identities.resolve_username("new_alice")["telegramID"] == "42"


def test_reassigned_and_removed_usernames_are_checked_against_current_user():
    identities.remember_user({"id": 42, "username": "alice"})
    identities.remember_user({"id": 43, "username": "alice"})
    assert identities.resolve_username("alice")["telegramID"] == "43"
    identities.remember_user({"id": 43})
    assert identities.resolve_username("alice") is None


def test_bot_accounts_are_not_participants():
    identities.remember_user({"id": 44, "username": "bot", "is_bot": True})
    assert identities.resolve_username("bot") is None
