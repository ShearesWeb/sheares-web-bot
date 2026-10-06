import os
from dataclasses import dataclass
from functools import cache


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    users_table_name: str
    laundry_table_name: str


@cache
def settings():
    """Reads the environment on first use so modules import without a full environment (tests, tooling)."""
    return Settings(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        users_table_name=os.environ["USERS_TABLE_NAME"],
        laundry_table_name=os.environ["LAUNDRY_TABLE_NAME"],
    )
