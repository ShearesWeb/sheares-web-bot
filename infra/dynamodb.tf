
locals {
  users_table_name   = "${var.env}-db-table-${var.project_code}-users"
  laundry_table_name = "${var.env}-db-table-${var.project_code}-laundry-machines"
}

# One item per (user, record type): profile = "PROFILE" holds block/level/year,
# profile = "LAUNDRY" holds the set of machines the user currently has claimed.
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
