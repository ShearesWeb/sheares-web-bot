# Sheares Bot

Sheares Bot gives residents a friendly Telegram interface for interacting within their block, automating the day-to-day routines they share, for instance laundry machines and sticker packs. Provisioned on AWS using Lambda, API Gateway, DynamoDB and EventBridge Scheduler, with Terraform.

## System Architecture

![AWS Infrastructure Diagram](aws_infra.png)

The bot is built entirely on AWS using an event-driven, serverless architecture.

- **Ingestion:** A Shearite sends a message to the Telegram Bot, which forwards the update via webhook to an API Gateway.
- **Bot Lambda:** API Gateway invokes the single bot function. `src/app.py` sees a webhook event, routes the update to the matching handler, and returns 200 to Telegram once it has been handled. Failures are logged and still acknowledged so Telegram does not retry them forever.
- **Storage:** Handlers read and write user and machine state in DynamoDB.
- **Scheduling:** Laundry actions create one-off EventBridge schedules. Group bill reminders use recurring schedules that are removed when disabled or fully paid.
- **Notification:** When the schedule fires, EventBridge invokes the same function with a job event (`{"job": "laundry_done", "payload": {...}}`), and the job handler messages the user.

The diagram still shows the earlier three-function layout.

## Database Schema (DynamoDB)

The bot utilizes Amazon DynamoDB for fast, serverless state management. The data is organized into two primary tables:

### 1. Users Table (`<env>-db-table-sheareswebbot-users`)

Stores resident profiles and tracks their active facility usage.

- **Partition Key (PK):** `userID` (The user's unique Telegram ID)
- **Sort Key (SK):** `profile` (The type of record, e.g., `PROFILE` for user demographics and block info, or `LAUNDRY` to track a set of machines currently claimed by the user)

### 2. Laundry Machines Table (`<env>-db-table-sheareswebbot-laundry-machines`)

Maintains the real-time state and ownership of all washing machines and dryers across the hall.

- **Partition Key (PK):** `block` (The hall block, e.g., `A`, `B`, `C`)
- **Sort Key (SK):** `machineID` (The specific machine identifier, e.g., `W1`, `D3`)

## Code Structure

```
src/
  app.py          Lambda entrypoint and local polling entrypoint; routing only
  core/           config, router, Telegram client, schedulers, DynamoDB tables
  features/
    profile/      handlers.py, repository.py
    laundry/      handlers.py, service.py, repository.py
    split/        expense collection, Textract extraction, bill allocation and sessions
tests/            pytest suite (moto for AWS, a fake Telegram client)
```

Each feature separates Telegram handlers, rules, and persistence. `handlers.py` owns message text, keyboards, and parsing `callback_data`; split payment views live in `payments.py`. `service.py` holds the rules and never imports Telegram. DynamoDB access lives in repository modules, including `split/ledger.py` for completed bills and payments. A feature without real rules (like `profile`) skips `service.py`.

All code ships in one Docker image (see `Dockerfile`) run by one Lambda with handler `app.lambda_handler`.

## Adding a Command

Register handlers with the decorators in `core/router.py`. A command or button handler receives a `Context` with `user_id`, `chat_id`, `text`, `data`, `message_id`, `telegram` and `scheduler`.

```python
from core.router import router


@router.command("/feedback")
def feedback_command(ctx):
    kb = [[{"text": "Hall events", "callback_data": "fb_events"}]]
    ctx.telegram.send_message(ctx.chat_id, "What is your feedback about?", kb)


@router.callback("fb_")
def feedback_topic(ctx):
    ctx.telegram.edit_message(ctx.chat_id, ctx.message_id, f"Thanks! Topic: {ctx.data[3:]}")
```

Callback handlers match on a `callback_data` prefix, and the longest matching prefix wins. Registering the same command or prefix twice fails at startup. For a new feature, create `src/features/<name>/` and add its `handlers` import to `src/features/__init__.py`. Scheduled work uses `@router.job("<name>")` with a `(payload, telegram)` handler and `ctx.scheduler.schedule(name, run_at, job, payload)`. Jobs that need to cancel or manage schedules use `@router.job("<name>", with_scheduler=True)` and receive `(payload, telegram, scheduler)`. Recurring work uses `schedule_recurring(name, interval_seconds, job, payload, start_at=first_run)`, where the optional first run must include a timezone. IAM allows creation of `Laundry_*` and `Split_*` schedules, and deletion of `Split_*` schedules.

## Running Locally

1.  Create your own bot with [@BotFather](https://t.me/BotFather) and copy its token. Do not use the preprod or prd bot: they have webhooks, and `make dev` refuses to poll a bot with a webhook.
2.  `cp .env.example .env` and fill it in. The table names point at real DynamoDB tables, so whatever you do locally changes those tables. Your AWS credentials need read and write access to them; uncomment `AWS_PROFILE` only if you use a named profile.
3.  `make install` once, then `make dev`. Message your bot in Telegram.

Reminders run in-process while `make dev` is running, so stopping it drops pending ones. Local polling retries connection failures and HTTP 5xx responses, including the startup webhook check, with delays of 1, 2, 4, 8, 16, then 30 seconds until Telegram responds. Successful requests reset the delay; retries preserve the update offset. Authentication errors and polling conflicts still stop the process rather than retrying indefinitely. Run the tests with `make test`; they use moto and need no AWS access.

## Current Commands

### Basic

- `/help` or `/start` – Displays the welcome message and available commands.
- `/profile` – Manages the user's hall profile. Users can create and view their profiles (TODO: Implement editing and adding extended profile information).

### Laundry Management

- `/laundry` – Displays a real-time summary of washing machine and dryer statuses for the user's specific block.
- `/wash` – Initiates a 30-minute washing cycle tracking session.
  - Allows users to bump/remind the previous user to collect their laundry if the machine is marked as completed but not emptied.
  - Allows users to logically move the previous user's clothes to another machine in the database to free up the current washer.
  - Provides a direct override option if the machine's digital state is desynced from reality.
- `/dry` – Initiates a drying cycle with selectable durations of 30, 45, or 60 minutes. Functions similarly to `/wash` with bump, move, and override capabilities.
- _Note: Both `/wash` and `/dry` will automatically notify the user to collect their items when the duration expires, regardless of whether their machine status was overridden by another user._

## Bill Splitting

Create a `/profile` before using bill splitting, balances, or reminders, as with laundry. This requirement applies to commands, uploads, and buttons, including private-chat links. Users without a saved profile receive "Please create a /profile first." Normal `/start`, `/help`, and `/profile` remain available.

`/split` starts a bill in private chat. When used in a group, it shows an **Open split privately** button. Tap it and press **Start** if Telegram prompts; `/start split` opens the split prompt directly. The split session starts in private chat at that point. Send text expenses such as `KFC - 20` and `Burger King - 36`, one per line, and/or receipt photos or JPEG/PNG files up to 10 MB. Each addition shows the full bill so far: text expenses first, followed by all numbered receipts with their items and totals. The `/done` prompt and final payment message also include the complete list. Accepted receipt uploads immediately show "Processing image..." before downloading and extracting items. That status changes to "Receipt added." when saved, or "Receipt wasn't added." if the addition fails; any error explanation is sent separately. Wait for a receipt to be acknowledged before sending the next one. Send `/done` after entering all expenses, or `/cancel` to stop. The main menu lists `/split`, `/bills`, and `/reminders` for bill splitting; `/done`, `/method`, and `/cancel` are omitted from that menu. The split flow explains when to use `/done` and `/cancel`.

After `/done`, choose **Equal**, **Exact amounts**, **Percentages**, or **By item**, then send all participant handles separated by spaces, commas, or new lines. Only those people are included; add your own handle if you want a share. Duplicate handles and IDs count once. Exact amounts and percentages show a numbered list of people: enter all allocation lines together in one message, using person numbers or their current `@handles`. Item splitting uses a checklist of buttons. `/done` repeats the current prompt or reopens the saved checklist; `/method` starts method and participant selection again before the bill is saved.

Use `/delete` during an unfinished split in private chat to open a deletion checklist. Numbered buttons list each text expense separately and each whole receipt with its final total, with text first and receipts below. Tap entries to check or uncheck them, then press **Done** to delete all selected entries together and show the consolidated bill with its updated total. If no entries remain, the split session is cancelled instead, with the same "Bill cancelled. Send /split to start again." message as `/cancel`. Selections persist across Previous and Next pages, and the checklist shows the selected count and amount. Nothing is deleted until **Done** is pressed; **Cancel** discards the selections and preserves the expenses and draft allocations. Deleting a receipt removes all its items, taxes, and service charges. When expenses remain, confirmed deletion returns the draft to expense collection and clears any method, participant, and item allocation choices; send `/done` to choose them again. Saved bills cannot be changed through `/delete`. Old buttons cannot delete a different option after the list changes, repeated checkbox callbacks do not toggle the selection twice, and duplicate Done taps cannot delete twice. Deletion does not extend the 15-minute window or refund receipt upload allowance.

### Split methods

- **Equal:** divide the full bill equally among the selected people. Any extra cents go to the first participants.
- **Exact amounts:** enter the final amount each person pays, including their tax and service charge. Every person must be listed exactly once (0 is allowed), and the amounts must match the bill total. For example, on a 56.00 bill:

  ```text
  @alice - 20.00
  @bob - 36.00
  ```

- **Percentages:** enter a percentage for every person, including 0% for no share. Percentages support up to four decimal places and must add up to exactly 100%:

  ```text
  @alice - 40%
  @bob - 60%
  ```

- **By item:** choose a person, then tap the item buttons to check or uncheck their items. Use **Choose another person** or **Next person** to switch. The same item can be checked for several people to share it equally. Choices persist across people, pages, and `/done`, for the lifetime of the active session. People lists show eight per page; item lists show six per page, with text expenses first and receipt items below. The checklist shows how many items have been assigned. Once all items have at least one owner, the people list previews each person's final share. Tap **Finish split** to save; unassigned items block completion. Only the creator can change or finish the checklist, including in groups. `/method` clears the draft selections and starts method and participant selection again.

  Each item number represents the full priced line, including its quantity. Receipt tax, service charges, discounts, and rounding differences are shared in proportion to each person's items on that receipt. Each receipt's printed final total is used once, so tax-inclusive prices are not taxed again. A receipt with unreadable or negative item prices needs a different split method; the bot explains this before accepting item assignments. Selected people with no assigned items have a zero share.

Amounts are stored in integer cents. Percentages and shared items use exact fractions internally; fractional cents are assigned by largest remainder, with participant order breaking ties. Item splits round separately for each receipt or text addition, so each addition's shares match its final total. Invalid totals, duplicate people, missing entries, and assignments to unselected people leave the bill open for correction. Checklist buttons set an explicit selection state, so duplicate callbacks do not undo a selection. Finishing checks the current draft revision to prevent saving stale shares during concurrent changes. Completed bills retain the chosen method, percentages or item assignments, final shares, and stable participant IDs. Allocation rules live in `features/split/allocation.py`; balances and payment clearing use the final shares for every method.

Telegram does not provide arbitrary private-user handle lookup through [getChat](https://core.telegram.org/bots/api#getchat). The bot remembers users from incoming messages and button clicks, and verifies current username mappings against stored IDs. Unknown participants can send `/start` to the bot, or the creator can use Telegram's [Choose people](https://core.telegram.org/bots/api#keyboardbuttonrequestusers) picker in a private chat. The picker can share up to 10 users per selection. Completed bills store stable user IDs, and payment messages link to those IDs. Sending `/done` again during the session redisplays the bill with the latest names the bot has observed. People without usernames appear by name.

Receipt images are downloaded from Telegram and sent directly to [AWS Textract AnalyzeExpense](https://docs.aws.amazon.com/textract/latest/dg/invoices-receipts.html), without S3 storage. The bot returns item names, quantities, and line totals, plus subtotal, service charge, tax, and total when detected. Currency prefixes are removed and subtotal excludes detected tax and service charge, including tax-inclusive receipts. Only priced entries become items. Separately quantified rows identify set components, so sets retain just the title beside their price. An item without those components keeps its full wrapped name, including unpriced continuation lines before the next priced entry. This distinction uses Textract's item fields and quantity positions, so inaccurate OCR grouping can still affect the result. Common quantity misreads such as `I` are corrected to `1`; quantities that remain nonnumeric are omitted. Each receipt contributes its final total once, including detected taxes and rounding. If the final total is unreadable and taxes are present, the bot asks for a manual expense instead of guessing. Receipt text is sent as plain text, so punctuation in item names cannot break Telegram formatting.

Items priced at zero are omitted. Booking carts keep the printed item title above their date, start time, end time, and price. Complete booking cards can also be recovered from Textract's OCR lines when its structured item rows are missing or incomplete. Their positions keep identical names and prices as separate items without counting a structured row twice. An unlabelled item price misclassified as a subtotal is ignored.

Receipt totals also accept Textract's `AMOUNT_DUE` field, explicitly labelled totals, and printed total lines from its OCR output when `TOTAL` is missing or unreadable. A field labelled **Total Due** is treated as a total even if Textract incorrectly classifies it as `SUBTOTAL`, preserving the actual subtotal. Blank duplicate total fields are ignored, including empty lines for handwritten tips below a printed **Total Due**. Tax and service charge rows preserve the printed labels, such as **GST**, **9% GST**, or **10% Service Charge**, in both receipt summaries and saved bills. When Textract supplies no label, the rows fall back to **Tax** and **Service charge**.

Sessions live in the existing users table under `profile = SPLIT#<chat_id>`. A session starts with a 15-minute window, which resets only after successfully saving text expenses or receipt items. Failed or empty uploads, commands, participant selection, and allocation buttons do not extend the deadline. An addition cannot revive a session if it expires while the addition is being processed. Sending text or a receipt after an unfinished session expires prompts: "There's no active split session. Send /split to start again." Expiry is enforced when handling updates; there is no automatic timeout message. Completed snapshots are kept under `profile = SPLIT_BILL#<session_id>`. A fresh `/split` replaces the active session and preserves completed snapshots. Duplicate Telegram updates are ignored, concurrent inputs are protected by a processing lease, and failed extraction can be retried while the session remains active. Bills support up to 200 items, 50 additions, and 50 participants. A final payment summary is posted in the creator's current chat.

### Receipt upload allowance

Each Telegram user can submit **20 receipt images per Singapore calendar day**, resetting at midnight. The allowance is stored in a separate `SPLIT_OCR_QUOTA` user record and applies across split sessions. Starting another split or changing a handle does not reset it. Conditional writes prevent simultaneous uploads from exceeding the limit. Accepted JPEG/PNG uploads count before downloading, including failed downloads or OCR attempts; unsupported file types and oversized uploads are rejected first. After reaching the limit, users can still add text expenses. Photos and documents in groups are ignored, including replies to payment reminders. In private chats, uploads are ignored unless that user has an unfinished split session in the same chat; this check runs before prompting for a profile. An expired unfinished session still receives the restart prompt described above. Ignored uploads are not downloaded or counted toward the allowance.

Receipt network timeouts are kept short: Telegram file metadata has a 3-second timeout, file download uses 2-second connect and 4-second read timeouts, and Textract uses 2-second connect and 8-second read timeouts without automatic retries. Lambda retains its 30-second timeout; API Gateway's integration timeout is unchanged.

### Balances and payments

Use `/bills` in a private chat with the bot to see your balances and open these views. In a group, `/bills` shows an **Open bills privately** button. Tapping it opens the bot's private chat; after **Start**, `/start bills` displays the bills menu directly. The link shows the balances of the person who opens it. The private-chat buttons use the bot's current username from `getMe`; no username configuration is required. A normal `/start` still shows the main menu.

- **What I owe** lists your unpaid shares, the person to pay, and a **Mark paid** button for each share.
- **Owed to me** lists unpaid shares owed to you, with **Mark received** buttons.
- **Paid history** shows the shares you have paid.
- **Received history** shows the payments received for bills you created.

These views and payment actions are accessed through the `/bills` buttons. The standalone `/owe`, `/owed`, and `/paid` commands, including `/paid <bill reference>`, are no longer registered.

The bill creator is recorded as the person who paid upfront. Each selected participant owes the creator their allocated share; the creator's own share and zero-value shares create no debt. Different bills remain separate, including when two people owe each other money. Lists show five shares per page, and totals include all pages.

Tapping **Mark paid** or **Mark received** first opens a confirmation screen. **Back** leaves the share unpaid and preserves reminders. Confirming clears the share from both people's outstanding balances and removes it from reminders. Either the participant or the bill creator can confirm; no second approval is required. This records a manually reported payment and does not transfer money. The original bill, amount, payment time, and the person who cleared it are retained in history. Repeated confirmations and redisplaying `/done` preserve the payment record. Records use Telegram IDs, so handle changes do not change balances.

The creator's `SPLIT_BILL#<session_id>` record holds the canonical payment status. Each debtor has a `SPLIT_PAYABLE#<session_id>` pointer to that record. Completing a new bill saves the session, completed snapshot, and all participant pointers in one DynamoDB transaction. The normal flow uses the existing GetItem, PutItem, UpdateItem, and Query permissions.

Payable pointers retain payment history and carry a `settled` flag, updated atomically with the canonical payment record. Outstanding views filter out settled pointers, then load matching bills with consistent `BatchGetItem` reads in batches of at most 100, retrying unprocessed keys with backoff. Apply the Terraform permission for `dynamodb:BatchGetItem` on the users table before deploying this change.

### Group payment reminders

After a split is completed and saved, the bot shows the completed bill followed by **Bill options**. The creator can choose:

- **Set up reminders** enables recurring reminders for this bill in a chosen group.
- **Generate payment message** creates a separate message in the current chat for the creator to forward. It names the person to pay, lists each unpaid person's amount, and shows the outstanding total. Generating it does not enable reminders or send messages to friends automatically. It uses the current payment records, so paid people are excluded.

These buttons work from the saved bill after the split session expires or another split is started. To manage active reminders later, use `/reminders`. The **Owed to me** view contains payment controls without a **Reminder / share** button. Only the bill creator can use reminder controls; forwarding the generated payment message does not forward setup controls.

Use `/reminders` to see all active reminders you created for bills with unpaid shares, five per page. Each entry shows the target group, bill description, unpaid-person count, and outstanding amount. Selecting an entry displays the current unpaid shares, group, next scheduled reminder, and the actions **Stop reminder**, **Generate payment message**, and **Change reminder group**, plus **Back to all reminders** to return to the list page you came from. The group-selection confirmation shows the same three reminder actions. Viewing an entry preserves its existing schedule and sends no additional group reminder. Fully paid bills and stopped or paused reminders are excluded. After stopping a reminder, **Back to all reminders** returns to the list. In groups, `/reminders` offers an **Open reminders privately** button; `/start reminders` opens the list in private chat. The old `/remind` command remains removed.

For reminder setup in private chat, choose **Set up reminders**, then **Choose reminder group**. Telegram's group picker shows groups where the bot is already a member. If the completed bill's **Set up reminders** button is used inside a group, that group is selected directly. The bot checks that it can access and post in the chosen group before enabling reminders. There is no need to type a group ID or maintain a directory of groups.

The private confirmation says only "Group selected." with the reminder action buttons. Open `/reminders` to view the group, unpaid shares, and schedule details. The bot edits the acknowledgement to add the controls instead of sending another confirmation. Initial delivery failures still include an explanation and retry option.

Every person with an unpaid share must be verifiable as a member of the chosen group. The bot checks membership during setup and before each reminder. If someone leaves or membership cannot be verified, no amounts are posted and reminders are paused; the creator receives a private notice. [Telegram's membership API](https://core.telegram.org/bots/api#getchatmember) only guarantees checks of other users when the bot is a group administrator, so grant that role if membership verification fails.

Selecting a group enables reminders and **sends an initial reminder to the group immediately**, with no frequency picker. Subsequent reminders run **every two days at 12:00 pm Singapore time (Asia/Singapore)**, starting at noon two calendar days after setup; for example, Monday setup sends an immediate message, followed by Wednesday noon and Friday noon reminders. Each bill has one target group; **Change reminder group** replaces the previous schedule, sends an initial reminder to the newly chosen group, and restarts this timetable. The initial post has a separate delivery claim, so repeated delivery of the same setup or retry action does not send it again or consume the next noon reminder. If the initial post encounters a temporary failure, the recurring schedule remains enabled and the creator gets a **Retry initial reminder** button. If group posting is forbidden, reminders are paused until the group is configured again.

Each reminder lists only unpaid shares, with the person to pay and the remaining amounts. Participants mark their shares paid through `/bills` → **What I owe** in private chat, or the creator marks them received through `/bills` → **Owed to me**. After the last outstanding share is marked paid, the bot disables and cancels the reminder. Every scheduled run also rereads the bill before posting, so paid people are excluded and fully paid bills produce no reminder. Payment status is the manually recorded status in the bot.

Use **Stop reminder** in the bill options or setup confirmation to stop a bill's reminders manually. Starting another setup replaces any older pending setup for that creator; setup requests expire after 15 minutes. If the bot is removed or posting is forbidden, reminders are paused. Reopen the bill options and select **Set up reminders** to choose the group again.

Reminder configuration is stored in the completed bill's `reminder` map. `SPLIT_REMINDER_SETUP` stores the creator's pending group selection. Schedule generations prevent old or replaced schedules from posting, and a conditional claim suppresses repeat deliveries for the same interval. Transient Telegram failures can be retried; an ambiguous network timeout can still result in a duplicate message because Telegram does not expose a send-message idempotency key.

Reminders have a **4-week limit**: one immediate message and at most 14 scheduled noon messages, through day 28. Setup explains the limit and `/reminders` shows the end date. EventBridge receives an explicit `EndDate` and deletes the schedule after completion; local timers also stop at the end. The bot disables reminders after the final scheduled message and notifies the creator privately. Expired reminders are also checked on list views and job delivery, so overdue jobs cannot post. Expiry preserves unpaid balances in `/bills`. Choosing a group again starts a new reminder period.

Apply the Terraform IAM updates before deploying reminders: `scheduler:CreateSchedule` must allow `Split_*` schedules, and `scheduler:DeleteSchedule` must allow that prefix. The existing scheduler execution role and `iam:PassRole` permission are reused. No new DynamoDB table or index is needed. Local runs use recurring timers in the current process and send from the local bot; stopping that process drops the timers. Re-enable reminders after restarting a local run. Deployed EventBridge schedules persist independently of the bot process.

### Receipt development tool

This first release uses the current bill and reminder schemas without upgrade paths. Recreate bills and reminders from earlier development drafts when testing the release.

Apply the updated Terraform IAM policy (`textract:AnalyzeExpense`) before deploying this feature. Local AWS credentials also need `textract:AnalyzeExpense`, plus the existing DynamoDB permissions for the Telegram flow. Textract uses the configured AWS region.

To analyze explicit local JPEG/PNG files through live Textract without Telegram or DynamoDB, configure AWS credentials and run the development tool from the project root in PowerShell:

```powershell
$env:PYTHONPATH = "src"
$env:AWS_DEFAULT_REGION = "ap-southeast-1"
.\.venv\Scripts\python.exe scripts/scan_receipts.py path/to/receipt.jpg
```

The tool requires image paths and has no bundled sample-folder default. It makes live Textract calls outside the bot's per-user allowance; automated tests stub AWS responses and make no live calls. OCR may miss or misread items, so check the output against the receipt. [Textract does not support Chinese text detection](https://docs.aws.amazon.com/textract/latest/dg/limits-document.html); mixed receipts may lose Chinese item names even when their English descriptions can be read.

## Roadmap & Idea Functions

- `/book` – A facility booking system for block amenities (e.g., lounges, sports equipment).
- `/flirt` – An anonymous messaging feature to connect residents.
- `/feedback` – A direct, anonymous feedback channel for hall events and infrastructure reporting.
