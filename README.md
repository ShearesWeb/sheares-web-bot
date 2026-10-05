# Sheares Bot

Sheares Bot is a serverless Telegram bot designed to streamline hall life and manage shared facilities for Sheares Hall residents.

## System Architecture

![AWS Infrastructure Diagram](aws_infra.png)

The bot is built entirely on AWS using an event-driven, serverless architecture. 

*   **Ingestion:** A Shearite sends a message to the Telegram Bot, which forwards the request via webhook to an API Gateway.
*   **Receiver:** The API Gateway triggers the `Receiver` Lambda function. To prevent timeouts and ensure quick webhook acknowledgement, this Lambda simply takes the incoming payload and pushes it into an SQS Message Processing Queue.
*   **Processing & Storage:** The `Processor` Lambda polls the SQS queue and executes the core business logic, maintaining state and user data by reading from and writing to DynamoDB. 
*   **Scheduling:** When a user starts a time-delayed action (like a laundry cycle), the `Processor` creates a scheduled schedule in EventBridge. 
*   **Notification:** Once the timer expires, EventBridge triggers the `Notifier` Lambda, which sends an alert directly back to the user through the Telegram Bot.

## Database Schema (DynamoDB)

The bot utilizes Amazon DynamoDB for fast, serverless state management. The data is organized into two primary tables:

### 1. Users Table (`preprod-db-table-shearesbot-users`)
Stores resident profiles and tracks their active facility usage.
*   **Partition Key (PK):** `userID` (The user's unique Telegram ID)
*   **Sort Key (SK):** `profile` (The type of record, e.g., `PROFILE` for user demographics and block info, or `LAUNDRY` to track a set of machines currently claimed by the user)

### 2. Laundry Machines Table (`preprod-db-table-shearesbot-laundry-machines`)
Maintains the real-time state and ownership of all washing machines and dryers across the hall.
*   **Partition Key (PK):** `block` (The hall block, e.g., `A`, `B`, `C`)
*   **Sort Key (SK):** `machineID` (The specific machine identifier, e.g., `W1`, `D3`)

## Code Structure

The repository is organized by Lambda functions, with dependencies bundled directly into each function's directory for easy deployment.

*   **`src/<lambda_name>/lambda_function.py`**: The main entry point for each Lambda function.
*   **`src/<lambda_name>/modules/`**: The directory where all external Python dependencies (e.g., `requests`, `boto3` wrappers) are installed and packaged alongside the deployment code.

## Current Commands

### Basic
*   `/help` or `/start` – Displays the welcome message and available commands.
*   `/profile` – Manages the user's hall profile. Users can create and view their profiles (TODO: Implement editing and adding extended profile information).

### Laundry Management
*   `/laundry` – Displays a real-time summary of washing machine and dryer statuses for the user's specific block.
*   `/wash` – Initiates a 30-minute washing cycle tracking session. 
    *   Allows users to bump/remind the previous user to collect their laundry if the machine is marked as completed but not emptied.
    *   Allows users to logically move the previous user's clothes to another machine in the database to free up the current washer.
    *   Provides a direct override option if the machine's digital state is desynced from reality.
*   `/dry` – Initiates a drying cycle with selectable durations of 30, 45, or 60 minutes. Functions similarly to `/wash` with bump, move, and override capabilities.
*   *Note: Both `/wash` and `/dry` will automatically notify the user to collect their items when the duration expires, regardless of whether their machine status was overridden by another user.*

## Roadmap & Idea Functions

*   `/book` – A facility booking system for block amenities (e.g., lounges, sports equipment).
*   `/flirt` – An anonymous messaging feature to connect residents.
*   `/feedback` – A direct, anonymous feedback channel for hall events and infrastructure reporting.