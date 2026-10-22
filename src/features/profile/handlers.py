from core.router import router
from features.profile import repository

BLOCKS = ["A", "B", "C", "D", "E", "F"]

HELP_TEXT = """
🤖🧡💙 *Welcome to Sheares Bot!*

Here are some basic commands to get you started:

👤 *Account*
• /profile - Set your block, level, and year

💸 *Bill Splitting*
• /split - Build a bill from text expenses and receipt photos (private chat)
• /bills - What you owe, what you're owed, and paid/received history (private chat)
• /reminders - View and manage pending group reminders (private chat)

🧺 *Laundry*
• /laundry - Status of laundry machines in your block
• /wash - Track your washing progress
• /dry - Track your drying progress

_More features coming soon!_
            """


@router.command("/help")
def help_command(ctx):
    ctx.telegram.send_message(ctx.chat_id, HELP_TEXT)


@router.command("/start")
def start_command(ctx):
    args = ctx.text.split()[1:]
    if ctx.chat_type == "private" and args == ["split"]:
        # Import on use: feature modules register their handlers at startup.
        from features.split.handlers import split_command
        split_command(ctx)
    elif ctx.chat_type == "private" and args == ["bills"]:
        from features.split.payments import bills_command
        bills_command(ctx)
    elif ctx.chat_type == "private" and args in (["reminders"], ["reminder"]):
        from features.split.reminders import reminder_command
        reminder_command(ctx)
    else:
        help_command(ctx)


@router.command("/profile")
def profile_command(ctx):
    profile = repository.get_profile(ctx.user_id)
    if not profile:
        keyboard = [[
            {"text": "Yes", "callback_data": "profile_create_yes"},
            {"text": "No", "callback_data": "profile_create_no"},
        ]]
        ctx.telegram.send_message(ctx.chat_id, "You don't have a profile yet. Would you like to create one?", keyboard)
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
    ctx.telegram.send_message(ctx.chat_id, details)


@router.callback("profile_create_no")
def cancel_creation(ctx):
    ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, "Profile creation cancelled.")


@router.callback("profile_create_yes")
def choose_block(ctx):
    keyboard = [
        [{"text": b, "callback_data": f"profile_blk_{b}"} for b in BLOCKS[:3]],
        [{"text": b, "callback_data": f"profile_blk_{b}"} for b in BLOCKS[3:]],
    ]
    ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, "Select your Block:", keyboard)


## TODO: Also get INTRANET email in profile for future integrations
## ... and perhaps other Optional profile info like Major, role in block etc
@router.callback("profile_blk_")
def choose_level(ctx):
    block = ctx.data.split("_")[2]
    keyboard = [
        [{"text": str(i), "callback_data": f"profile_lvl_{block}_{i}"} for i in range(1, 5)],
        [{"text": str(i), "callback_data": f"profile_lvl_{block}_{i}"} for i in range(5, 9)],
    ]
    ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, f"Block {block} selected.\nNow select your Level:", keyboard)


@router.callback("profile_lvl_")
def choose_year(ctx):
    parts = ctx.data.split("_")
    block, level = parts[2], parts[3]
    keyboard = [[{"text": str(i), "callback_data": f"profile_yr_{block}_{level}_{i}"} for i in range(1, 7)]]
    ctx.telegram.edit_message(
        ctx.chat_id, ctx.message_id,
        f"Block {block}, Level {level} selected.\nFinally, select your Year of Study:", keyboard,
    )


@router.callback("profile_yr_")
def save_profile(ctx):
    parts = ctx.data.split("_")
    block, level, year = parts[2], parts[3], parts[4]
    repository.put_profile(ctx.user_id, ctx.chat_id, block, int(level), int(year))
    ctx.telegram.edit_message(
        ctx.chat_id, ctx.message_id,
        f"✅ Profile created successfully!\nBlock: {block}\nLevel: {level}\nYear: {year}",
    )
