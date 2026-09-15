locals {
  dynamodb_table_name = "${var.env}-db-table-${var.project_code}"
}

resource "aws_dynamodb_table" "table" {
  name = local.dynamodb_table_name

  # On-demand billing, read_capacity and write_capacity left empty. billing_mode can be either "PROVISIONED" or "PAY_PER_REQUEST"
  billing_mode = ""
  read_capacity  =
  write_capacity =

  # Every item in the table uses PK (partition key) + SK (sort key).
  # Different features (rooms, laundry, CCA, etc.) just use different
  # prefixes, e.g. "ROOM#..." or "CCA#...".
  hash_key  = "PK"
  range_key = "SK"

  # Only key attributes need declaring here (DynamoDB is schemaless
  # otherwise) — PK/SK for the table, GSI1PK/GSI1SK for the index below.
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

  # Able to look up everything for one resident in a single query
  # (GSI1PK = "USER#<id>"), no matter which feature the item belongs to.
  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }
}
