from shared import user_table, user_profile_key, send_message, edit_message


## COMMAND HANDLERS
def handle_profile_command(user_id, chat_id):
    response = user_table.get_item(Key=user_profile_key(user_id))
    profile = response.get('Item')
    
    if not profile:
        # User doesn't exist, prompt creation
        keyboard = [
            [
                {"text": "Yes", "callback_data": "profile_create_yes"},
                {"text": "No", "callback_data": "profile_create_no"}
            ]
        ]
        send_message(chat_id, "You don't have a profile yet. Would you like to create one?", keyboard)
        return

    details = (
        f"👤 *Your Profile*\n"
        f"Block: *{profile.get('block')}{profile.get('level')}*\n"
        f"Year: *{profile.get('year')}*\n"
        f"-------------------------\n"
        f"*Washing Machine stats*\n"
        f"No. of recorded washing cycles: *N* (*X minutes*)\n"
        f"No. of recorded drying cycles: *N* (*X minutes*)\n"
        f"Total spent: ($Y)"
    )
    send_message(chat_id, details)


def handle_help_command(chat_id):
    helpstr = """
🤖🧡💙 *Welcome to Sheares Bot!*

Here are some basic commands to get you started:

👤 *Account*
• /profile - Set your block, level, and year

🧺 *Laundry*
• /laundry - Status of laundry machines in your block
• /wash - Track your washing progress
• /dry - Track your drying progress

_More features coming soon!_
            """

    send_message(chat_id, helpstr)


## CALLBACK HANDLERS
def handle_callback(user_id, chat_id, message_id, data):
    # "NO" to create profile
    if data == "profile_create_no":
        edit_message(chat_id, message_id, "Profile creation cancelled.")
        return

    # "YES" to create profile
    # 1) Select Block
    if data == "profile_create_yes":
        blocks = ['A', 'B', 'C', 'D', 'E', 'F']
        keyboard = [
            [{"text": b, "callback_data": f"profile_blk_{b}"} for b in blocks[:3]],
            [{"text": b, "callback_data": f"profile_blk_{b}"} for b in blocks[3:]]
        ]
        edit_message(chat_id, message_id, "Select your Block:", keyboard)
        return

    ## TODO: Also get INTRANET email in profile for future integrations
    ## ... and perhaps other Optional profile info like Major, role in block etc
    # 2) Select Level
    if data.startswith("profile_blk_"):
        block = data.split("_")[2] 

        keyboard = [
            [{"text": str(i), "callback_data": f"profile_lvl_{block}_{i}"} for i in range(1, 5)],
            [{"text": str(i), "callback_data": f"profile_lvl_{block}_{i}"} for i in range(5, 9)]
        ]
        edit_message(chat_id, message_id, f"Block {block} selected.\nNow select your Level:", keyboard)
        return

    # 3) Select Year
    if data.startswith("profile_lvl_"):
        parts = data.split("_")
        block = parts[2]
        level = parts[3]
        
        # Create 1 row of 6 buttons for years 1-6
        keyboard = [[{"text": str(i), "callback_data": f"profile_yr_{block}_{level}_{i}"} for i in range(1, 7)]]
        edit_message(chat_id, message_id, f"Block {block}, Level {level} selected.\nFinally, select your Year of Study:", keyboard)
        return

    # Save to DB (rmb to verify non callback items)
    if data.startswith("profile_yr_"):
        parts = data.split("_")
        block = parts[2]
        level = parts[3]
        year = parts[4]
        
        # Save to DynamoDB
        user_table.put_item(
            Item={
                'userID': f'USER#{user_id}',
                'chatID': str(chat_id), 
                'profile': 'PROFILE',
                'block': block,
                'level': int(level),
                'year': int(year)
            }
        )
        edit_message(chat_id, message_id, f"✅ Profile created successfully!\nBlock: {block}\nLevel: {level}\nYear: {year}")
        return

