import os
import boto3
import requests

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Database Setup
USER_TABLE_NAME    = "preprod-db-table-shearesbot-users"
LAUNDRY_TABLE_NAME = "preprod-db-table-shearesbot-laundry-machines"

dynamodb = boto3.resource('dynamodb')
user_table    = dynamodb.Table(USER_TABLE_NAME)
laundry_table = dynamodb.Table(LAUNDRY_TABLE_NAME)

# Shared identity helpers. Telegram user IDs identify people; chat IDs route messages.
def user_profile_key(user_id):
    return {'userID': f'USER#{user_id}', 'profile': 'PROFILE'}


def get_notification_chat(machine):
    """Return the owner's recorded chat, including records written before this change."""
    if not machine or machine.get('currentUser') in (None, 'NONE'):
        return None
    if 'currentChatID' in machine:
        chat_id = machine['currentChatID']
        return None if chat_id in (None, 'NONE') else chat_id
    # Legacy records stored the notification chat in currentUser.
    return machine.get('currentUser')


# Shared Telegram Helpers
def send_message(chat_id, text, keyboard=None):
    payload = {
        "chat_id": chat_id, 
        "text": text,
        "parse_mode": "Markdown"  # Enables *bold*, _italic_, etc.
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)

def edit_message(chat_id, message_id, text, keyboard=None):
    payload = {
        "chat_id": chat_id, 
        "message_id": message_id, 
        "text": text,
        "parse_mode": "Markdown"
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    requests.post(f"{TELEGRAM_API_URL}/editMessageText", json=payload)

