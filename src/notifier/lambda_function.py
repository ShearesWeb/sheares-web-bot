import os
import json
import urllib3

http = urllib3.PoolManager()
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# Triggered by EventBridge
def lambda_handler(event, context):
    chat_id = event.get('chat_id')
    text = event.get('text')
    keyboard = event.get('keyboard') 
    
    if not chat_id or not text:
        return {"statusCode": 400, "body": "Missing chat_id or text"}

    # Build generic payload
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    
    # Send directly to Telegram
    http.request(
        'POST',
        TELEGRAM_API_URL,
        body=json.dumps(payload),
        headers={'Content-Type': 'application/json'}
    )
    
    return {"statusCode": 200, "body": "Notification sent"}