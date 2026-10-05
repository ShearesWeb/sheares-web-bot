
    
  
import os
import sys

# The image runs this as processor.lambda_function, so the sibling modules
# (shared, laundry, user_profile) aren't importable as top-level names otherwise.
sys.path.insert(0, os.path.dirname(__file__))

import user_profile.handlers as profile
import laundry.handlers as laundry

def lambda_handler(event, context):
    # The receiver async-invokes this function with the raw Telegram update as the payload.
    update = event

    # ROUTER: Text Commands
    if 'message' in update:
        message = update['message']
        sender = message.get('from')
        # Anonymous/chat senders do not expose a real user identity.
        if not sender or message.get('sender_chat'):
            return
        user_id = sender['id']
        chat_id = message['chat']['id']
        text = message.get('text', '')

        if text.startswith('/help') or text.startswith('/start'):
            profile.handle_help_command(chat_id)
        elif text.startswith('/profile'):
            profile.handle_profile_command(user_id, chat_id)
        elif text.startswith('/laundry'):
            laundry.handle_laundry_command(user_id, chat_id)
        elif text.startswith('/wash'):
            laundry.handle_wash_command(user_id, chat_id)
        elif text.startswith('/dry'):
            laundry.handle_dry_command(user_id, chat_id)
        else:
            pass
    
    # ROUTER: Button Clicks (Inline Keyboards)
    elif 'callback_query' in update:
        callback = update['callback_query']
        # The button clicker is callback.from, not the bot that sent the message.
        user_id = callback['from']['id']
        if not callback.get('message') or 'data' not in callback:
            return
        chat_id = callback['message']['chat']['id']
        message_id = callback['message']['message_id']
        data = callback['data']
        
        # Profile callbacks
        if data.startswith('profile_'):
            profile.handle_callback(user_id, chat_id, message_id, data)
        
        # Laundry callbacks (Using a tuple to catch all prefixes in one line)
        elif data.startswith(('drydur_', 'mclick_', 'bump_', 'move_', 'moveto_', 'over_', 'moveride_', 'cancel', 'collect_')):
            laundry.handle_laundry_callback(user_id, chat_id, message_id, data)
