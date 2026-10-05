import os
import time
import json
import boto3
from math import ceil
from datetime import datetime, UTC, timedelta
from boto3.dynamodb.conditions import Key
from shared import user_table, laundry_table, user_profile_key, get_notification_chat, send_message, edit_message

# Eventbridge
scheduler = boto3.client('scheduler')
NOTIFIER_FUNCTION_ARN = os.environ["NOTIFIER_FUNCTION_ARN"]
SCHEDULER_ROLE_ARN    = os.environ["SCHEDULER_ROLE_ARN"]

# Machines exist physically whether or not anyone has used them yet, so they
# have no DynamoDB row until their first claim.
MACHINE_IDS = [f"{typ}{i}" for typ in ('W', 'D') for i in range(1, 5)]

## Tracking Helpers
def add_user_machine(user_id, machine_id):
    if not user_id or user_id == 'NONE': return
    user_table.update_item(
        Key={'userID': f'USER#{user_id}', 'profile': 'LAUNDRY'},
        UpdateExpression="ADD machines :m",
        ExpressionAttributeValues={':m': set([machine_id])}
    )

def remove_user_machine(user_id, machine_id):
    if not user_id or user_id == 'NONE': return
    user_table.update_item(
        Key={'userID': f'USER#{user_id}', 'profile': 'LAUNDRY'},
        UpdateExpression="DELETE machines :m",
        ExpressionAttributeValues={':m': set([machine_id])}
    )

## General Helpers
def get_user_block(user_id):
    resp = user_table.get_item(Key=user_profile_key(user_id))
    return resp.get('Item', {}).get('block')

def get_machine(block, machine_id):
    resp = laundry_table.get_item(Key={'block': block, 'machineID': machine_id})
    return resp.get('Item')

def get_real_machine_status(machine):
    if not machine: return 'AVAILABLE'
    status = machine.get('status', 'AVAILABLE')
    end_time = int(machine.get('endTime', 0))
    if status == 'IN_USE' and end_time > 0 and int(time.time()) >= end_time:
        return 'COMPLETED'
    return status

def claim_machine_and_schedule(user_id, chat_id, block, machine_id, duration_mins):
    # Check if we are overwriting someone else's cycle and clear their tracker
    old_machine = get_machine(block, machine_id)
    old_user = old_machine.get('currentUser', 'NONE') if old_machine else 'NONE'
    if old_user != 'NONE' and old_user != str(user_id):
        remove_user_machine(old_user, machine_id)
        
    # Track machine for current user
    add_user_machine(user_id, machine_id)
    
    # 1. Update DB
    end_time = int(time.time()) + (duration_mins * 60)
    laundry_table.put_item(
        Item={
            'block': block,
            'machineID': machine_id,
            'status': 'IN_USE',
            'currentUser': str(user_id),
            'currentChatID': str(chat_id),
            'endTime': end_time,
            'type': 'WASHER' if 'W' in machine_id else 'DRYER'
        }
    )
    
    # 2. Schedule the notifier Lambda
    run_at = (datetime.now(UTC) + timedelta(minutes=duration_mins)).strftime('%Y-%m-%dT%H:%M:%S')
    target_payload = {
        "chat_id": chat_id,
        "text": f"🔔 Your laundry in {machine_id} (Block {block}) is done!",
        "keyboard": [[
            {"text": "Collected ✅", "callback_data": f"collect_{machine_id}"}
        ]]
    }
    
    scheduler.create_schedule(
        Name=f"Laundry_{block}_{machine_id}_{int(time.time())}",
        ScheduleExpression=f"at({run_at})",
        Target={
            'Arn': NOTIFIER_FUNCTION_ARN,
            'RoleArn': SCHEDULER_ROLE_ARN,
            'Input': json.dumps(target_payload)
        },
        FlexibleTimeWindow={'Mode': 'OFF'},
        ActionAfterCompletion='DELETE'
    )

## Command Handlers
def handle_wash_command(user_id, chat_id):
    block = get_user_block(user_id)
    if not block:
        return send_message(chat_id, "Please create a /profile first.")
    
    kb = [[{"text": f"W{i}", "callback_data": f"mclick_W{i}_30"} for i in range(1, 5)]]
    kb.append([{"text": "❌ Cancel", "callback_data": "cancel"}])
    send_message(chat_id, "Which washing machine would you like to use? (30 mins)", kb)

def handle_dry_command(user_id, chat_id):
    if not get_user_block(user_id):
        return send_message(chat_id, "Please create a /profile first.")
    
    kb = [[
        {"text": "30 mins", "callback_data": "drydur_30"},
        {"text": "45 mins", "callback_data": "drydur_45"},
        {"text": "60 mins", "callback_data": "drydur_60"}
    ]]
    kb.append([{"text": "❌ Cancel", "callback_data": "cancel"}])
    send_message(chat_id, "How long do you need the dryer for?", kb)

def handle_laundry_command(user_id, chat_id):
    response = user_table.get_item(Key=user_profile_key(user_id))
    profile = response.get('Item')

    if not profile:
        send_message(chat_id, "Create a profile with /profile to get started!")
        return

    block = profile.get('block')
    
    try:
        response = laundry_table.query(KeyConditionExpression=Key('block').eq(block))
        machines = response.get('Items', [])
        
        # Fetch user's active machines
        laundry_profile = user_table.get_item(Key={'userID': f'USER#{user_id}', 'profile': 'LAUNDRY'}).get('Item', {})
        my_machines = laundry_profile.get('machines', set())
        
    except Exception as e:
        print(f"DynamoDB Query Error: {e}")
        send_message(chat_id, "⚠️ Error retrieving laundry status.")
        return

    machines_by_id = {m['machineID']: m for m in machines}
    machines = [machines_by_id.get(m_id, {'machineID': m_id}) for m_id in MACHINE_IDS]

    status_map = {
        'AVAILABLE': '🟢 Available',
        'IN_USE'   : '🔴 In use',
        'COMPLETED': '🟡 Awaiting collection'
    }

    washers = []
    dryers = []
    current_time = int(time.time())

    for m in machines:
        m_id = m['machineID']
        db_status = m.get('status', 'AVAILABLE')
        end_time = int(m.get('endTime', 0))
        
        # Dynamic check
        if db_status == 'IN_USE' and end_time > 0 and current_time >= end_time:
            db_status = 'COMPLETED'
            
        # Format status string and append (by you) flag ONLY if tracked
        status_text = status_map.get(db_status, '[Error]')
        if m_id in my_machines and db_status != 'AVAILABLE':
            status_text += " *(by you)*"
            
        display = f"*{m_id}*: {status_text}"
        
        # Time remaining calculation
        if db_status == 'IN_USE' and end_time > current_time:
            mins = ceil((end_time - current_time) / 60)
            display += f" (~{mins}mins left)"
        
        if m_id.startswith('W'):
            washers.append(display)
        else:
            dryers.append(display)

    # Format the bottom summary text
    if my_machines:
        # Sort so they appear cleanly (e.g., D1, W2)
        active_list = sorted(list(my_machines))
        summary_text = f"_You are currently using: {', '.join(active_list)}_"
    else:
        summary_text = "_You are not using any machines._"

    message_text = (
        f"₊˚⊹ 🧺 *Block {block} Laundry Room* 🫧 ⊹˚₊\n\n"
        f"*Washers*\n"
        f"{chr(10).join(washers)}\n\n"
        f"*Dryers*\n"
        f"{chr(10).join(dryers)}\n\n"
        f"{summary_text}"
    )

    send_message(chat_id, message_text)

## Callback Handlers
def handle_laundry_callback(user_id, chat_id, message_id, data):
    data = '_'.join(part.strip('*') for part in data.split('_'))
    if data == 'cancel':
        edit_message(chat_id, message_id, 'Action cancelled.')
        return

    block = get_user_block(user_id)
    if not block:
        send_message(chat_id, 'Please create a /profile first.')
        return
    
    # 1. Dryer Duration Selected
    if data.startswith('drydur_'):
        dur = data.split('_')[1]
        kb = [[{"text": f"D{i}", "callback_data": f"mclick_D{i}_{dur}"} for i in range(1, 5)]]
        kb.append([{"text": "❌ Cancel", "callback_data": "cancel"}])
        edit_message(chat_id, message_id, f"Which dryer would you like to use for {dur} mins?", kb)
        return

    # 2. Machine Clicked
    if data.startswith('mclick_'):
        parts = data.split('_')
        m_id, dur = parts[1], parts[2]
        machine = get_machine(block, m_id)
        status = get_real_machine_status(machine)
        
        if status == 'AVAILABLE':
            claim_machine_and_schedule(user_id, chat_id, block, m_id, int(dur))
            edit_message(chat_id, message_id, f"✅ *{m_id}* is yours for {dur} minutes. I'll remind you when it's done!")
            
        elif status == 'COMPLETED':
            msg = (
                f"*{m_id}* is currently awaiting collection. Send a reminder to the last user?\n"
                f"You can also move the contents to another machine.\n"
                f"_Don't see anything? Perhaps they forgot to click collect. Would you like to override?_"
            )
            kb = [
                [{"text": "🔔 Bump", "callback_data": f"bump_{m_id}"}],
                [{"text": "🔁 Move", "callback_data": f"move_{m_id}"}],
                [{"text": "⚠️ Override", "callback_data": f"over_{m_id}_{dur}"}],
                [{"text": "❌ Cancel", "callback_data": "cancel"}]
            ]
            edit_message(chat_id, message_id, msg, kb)
            
        elif status == 'IN_USE':
            kb = [
                [{"text": "⚠️ Yes, Override", "callback_data": f"over_{m_id}_{dur}"}],
                [{"text": "❌ Cancel", "callback_data": "cancel"}]
            ]
            edit_message(chat_id, message_id, f"⚠️ *{m_id}* is currently IN USE. Are you sure you want to override?", kb)
        return

    # 3. Bump User
    if data.startswith('bump_'):
        m_id = data.split('_')[1]
        machine = get_machine(block, m_id)
        owner_chat = get_notification_chat(machine)
        if owner_chat is None:
            edit_message(chat_id, message_id, f'⚠️ No registered user to remind for *{m_id}*.')
            return
        send_message(owner_chat, f"🔔 Reminder to take your clothes from *{m_id}* as someone is trying to use it!")
        edit_message(chat_id, message_id, f"✅ Reminder sent to the user of *{m_id}*.")
        return

    # 4. Move Contents (Select Target)
    if data.startswith('move_'):
        parts = data.split('_')
        m_id = parts[1]
        targets = []
        for typ in ['W', 'D']:
            for i in range(1, 5):
                target = f"{typ}{i}"
                if target != m_id:
                    targets.append({"text": target, "callback_data": f"moveto_{m_id}_{target}"})

        kb = [targets[i:i + 4] for i in range(0, len(targets), 4)]
        kb.append([{"text": "❌ Cancel", "callback_data": "cancel"}])
        edit_message(chat_id, message_id, f"Where are you moving the contents of *{m_id}* to?", kb)
        return 
    
    if data.startswith('moveto_'):
        parts = data.split('_')
        from_id, to_id = parts[1], parts[2]
        target_machine = get_machine(block, to_id)
        
        if get_real_machine_status(target_machine) == 'AVAILABLE':
            # Get original owner
            orig_machine = get_machine(block, from_id)
            if not orig_machine:
                edit_message(chat_id, message_id, f"*{from_id}* has nothing recorded in it. Use /wash or /dry to start your cycle.")
                return
            orig_user = orig_machine.get('currentUser', 'NONE')
            orig_chat = get_notification_chat(orig_machine) or 'NONE'
            
            # Update Trackers: Transfer ownership logic
            if orig_user != 'NONE':
                add_user_machine(orig_user, to_id)
                remove_user_machine(orig_user, from_id)
            
            # Transfer contents to new machine
            laundry_table.update_item(
                Key={'block': block, 'machineID': to_id},
                UpdateExpression="SET #s = :s, currentUser = :u, currentChatID = :c, endTime = :e",
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'COMPLETED', ':u': orig_user, ':c': orig_chat, ':e': 0}
            )
            
            # Free up the original machine
            laundry_table.update_item(
                Key={'block': block, 'machineID': from_id},
                UpdateExpression="SET #s = :s, currentUser = :u, currentChatID = :c, endTime = :e",
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'AVAILABLE', ':u': 'NONE', ':c': 'NONE', ':e': 0}
            )
            
            # Notify original user
            if orig_chat != 'NONE':
                send_message(orig_chat, f"🔔 Your clothes were moved from {from_id} to {to_id} by the next user.")
                
            edit_message(chat_id, message_id, f"✅ Contents moved to {to_id}. {from_id} is now empty. Use /wash or /dry to start your cycle.")
        else:
            kb = [
                [{"text": "⚠️ Override", "callback_data": f"moveride_{from_id}_{to_id}"}],
                [{"text": "❌ Cancel", "callback_data": "cancel"}]
            ]
            edit_message(chat_id, message_id, f"⚠️ {to_id} is occupied. You can cancel, or override the target machine.", kb)
        return

    # 6. Direct Overrides
    if data.startswith('over_'):
        parts = data.split('_')
        m_id, dur = parts[1], parts[2]
        
        # claim_machine_and_schedule will automatically remove the tracking for the old user if present
        claim_machine_and_schedule(user_id, chat_id, block, m_id, int(dur))
        edit_message(chat_id, message_id, f"✅ Overridden. *{m_id}* is yours for {dur} minutes.")
        return
        
    if data.startswith('moveride_'):
        parts = data.split('_')
        from_id, to_id = parts[1], parts[2]
        
        target_machine = get_machine(block, to_id)
        target_user = target_machine.get('currentUser', 'NONE') if target_machine else 'NONE'
        
        orig_machine = get_machine(block, from_id)
        if not orig_machine:
            edit_message(chat_id, message_id, f"*{from_id}* has nothing recorded in it. Use /wash or /dry to start your cycle.")
            return
        orig_user = orig_machine.get('currentUser', 'NONE')
        orig_chat = get_notification_chat(orig_machine) or 'NONE'
        
        # Update Trackers: Clear the overridden target user's tracker, swap original user's trackers
        if target_user != 'NONE':
            remove_user_machine(target_user, to_id)
        if orig_user != 'NONE':
            add_user_machine(orig_user, to_id)
            remove_user_machine(orig_user, from_id)
        
        # Transfer contents to target
        laundry_table.update_item(
            Key={'block': block, 'machineID': to_id},
            UpdateExpression="SET #s = :s, currentUser = :u, currentChatID = :c, endTime = :e",
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'COMPLETED', ':u': orig_user, ':c': orig_chat, ':e': 0}
        )
        
        # Free up the original machine
        laundry_table.update_item(
            Key={'block': block, 'machineID': from_id},
            UpdateExpression="SET #s = :s, currentUser = :u, currentChatID = :c, endTime = :e",
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'AVAILABLE', ':u': 'NONE', ':c': 'NONE', ':e': 0}
        )
        
        edit_message(chat_id, message_id, f"✅ Overridden and moved. {from_id} is now empty. Use /wash or /dry to start your cycle.")
        return
    
    # 7. User clicks "Collected"
    if data.startswith('collect_'):
        m_id = data.split('_')[1]
        machine = get_machine(block, m_id)
        
        # only clear if the current user still owns it because it could have been overridden
        if machine and str(machine.get('currentUser')) == str(user_id):
            
            # Clear machine from user tracker
            remove_user_machine(user_id, m_id)
            
            laundry_table.update_item(
                Key={'block': block, 'machineID': m_id},
                UpdateExpression="SET #s = :s, currentUser = :u, currentChatID = :c, endTime = :e",
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'AVAILABLE', ':u': 'NONE', ':c': 'NONE', ':e': 0}
            )
            edit_message(chat_id, message_id, f"✅ Thank you for collecting from *{m_id}*. The machine is now available.")
        else:
            # They took too long and someone else claimed/moved it
            edit_message(chat_id, message_id, f"✅ Thank you for collecting from *{m_id}*")
        return