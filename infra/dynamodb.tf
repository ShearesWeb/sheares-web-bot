
locals {
  users_table_name   = "${var.env}-db-table-${var.project_code}-users"
  laundry_table_name = "${var.env}-db-table-${var.project_code}-laundry-machines"
}

# One item per (user, record type): profile = "PROFILE" holds block/level/year,
# profile = "LAUNDRY" holds the set of machines the user currently has claimed.
# profile = "SPLIT#<chat_id>" holds that user's active bill in the chat.
# profile = "SPLIT_OCR_QUOTA" holds each user's Singapore-day image allowance.
# profile = "SPLIT_BILL#<session_id>" preserves completed bills and participant IDs.
# profile = "SPLIT_PAYABLE#<session_id>" links a participant to the creator's bill.
# Its settled flag filters balances without discarding payment-history pointers.
# Settlement status lives on the creator's bill, shared by both balance views.
# A bill's reminder map holds the target group, interval, generation and delivery slot.
# profile = "SPLIT_REMINDER_SETUP" holds one expiring reminder setup per creator.
# profile = "IDENTITY" holds a user's current Telegram name; USERNAME#<name>
# partitions point to stable user IDs without needing a secondary index.
resource "aws_dynamodb_table" "users" {
  name         = local.users_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "userID"
  range_key    = "profile"

  attribute {
    name = "userID"
    type = "S"
  }

  attribute {
    name = "profile"
    type = "S"
  }

  # Automatically clean up expired authentication tokens.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# One item per machine, partitioned by block so /laundry is a single query.
resource "aws_dynamodb_table" "laundry_machines" {
  name         = local.laundry_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "block"
  range_key    = "machineID"

  attribute {
    name = "block"
    type = "S"
  }

  attribute {
    name = "machineID"
    type = "S"
  }
}
