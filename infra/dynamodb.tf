# locals {
#   dynamodb_table_name = "${var.env}-db-table-${var.project_code}"
# }

# resource "aws_dynamodb_table" "table" {
#   name         = local.dynamodb_table_name
#   billing_mode = "PAY_PER_REQUEST"
#   hash_key     = "job_id"

#   attribute {
#     name = "job_id"
#     type = "S"
#   }
# }
