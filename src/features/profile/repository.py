from core.db import users_table


def user_profile_key(user_id):
    return {"userID": f"USER#{user_id}", "profile": "PROFILE"}


def get_profile(user_id):
    return users_table().get_item(Key=user_profile_key(user_id)).get("Item")


def put_profile(user_id, chat_id, block, level, year):
    users_table().put_item(Item={
        **user_profile_key(user_id),
        "chatID": str(chat_id),
        "block": block,
        "level": level,
        "year": year,
    })
