
import secrets
import time

from features.auth import repository

TOKEN_LIFETIME_SECONDS = 600


def create_link_request(telegram_user_id):
    token = secrets.token_urlsafe(32)

    repository.save_link_request(
        token=token,
        telegram_user_id=telegram_user_id,
        expires_at=int(time.time()) + TOKEN_LIFETIME_SECONDS,
    )

    return token


def consume_link_request(token):
    return repository.consume_link_request(token)


def complete_link_request(token, resident_id):
    return repository.complete_link_request(token, resident_id)
