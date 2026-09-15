locals {
  dynamodb_table_name = "${var.env}-db-table-${var.project_code}"
}

# Single-table design: PK/SK group items by their physical or organisational
# anchor (room, floor, cca, facility, sticker pack); GSI1 re-indexes every
# item by resident so "everything about this user" is one query regardless
# of feature. See SHW-17 for the full entity-to-key mapping.
resource "aws_dynamodb_table" "table" {
  name         = local.dynamodb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "GSI1PK"
    type = "S"
  }

  attribute {
    name = "GSI1SK"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }
}
