
locals {
  users_table_name      = "${var.env}-db-table-${var.project_code}-users"
  laundry_table_name    = "${var.env}-db-table-${var.project_code}-laundry-machines"
  facilities_table_name = "${var.env}-db-table-${var.project_code}-facilities"
}

# One item per (user, record type):
# profile = "PROFILE" holds block/level/year,
# profile = "LAUNDRY" holds the set of machines the user currently has claimed.
# profile = "FACILITY" holds the user's facility booking map.
# profile = "FACILITY_PENDING" holds an expiring request for booking times.
# profile = "SPLIT#<chat_id>" holds that user's active bill in the chat.
# profile = "SPLIT_BILL#<session_id>" preserves completed bills and participant IDs.
# profile = "SPLIT_PAYABLE#<session_id>" links a participant to the creator's bill.
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

resource "aws_dynamodb_table" "facilities" {
  name         = local.facilities_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "block"
  range_key    = "facilityID"

  attribute {
    name = "block"
    type = "S"
  }

  attribute {
    name = "facilityID"
    type = "S"
  }
}

# Catalog items stay separate from the mutable booking state so Terraform does
# not reset active bookings on a later apply. Reservation state uses keys such
# as BOOKING#LOUNGE in this same block-partitioned table.
locals {
  facility_blocks = ["A", "B", "C", "D", "E"]
  facility_names = {
    LOUNGE  = "Lounge"
    KITCHEN = "Kitchen"
    ROOFTOP = "Rooftop"
  }
  facility_catalog = flatten([
    for block in local.facility_blocks : [
      for facility_id, facility_name in local.facility_names : {
        block         = block
        facility_id   = facility_id
        facility_name = facility_name
      }
    ]
  ])
}

resource "aws_dynamodb_table_item" "facility" {
  for_each = {
    for facility in local.facility_catalog : "${facility.block}#${facility.facility_id}" => facility
  }

  table_name = aws_dynamodb_table.facilities.name
  hash_key   = "block"
  range_key  = "facilityID"
  item = jsonencode({
    block        = { S = each.value.block }
    facilityID   = { S = each.value.facility_id }
    facilityName = { S = each.value.facility_name }
    status       = { S = "FREE" }
  })

  # The catalog is bootstrapped by Terraform; DynamoDB status edits can mark
  # a facility unavailable without a later apply restoring it to FREE.
  lifecycle {
    ignore_changes = [item]
  }
}
