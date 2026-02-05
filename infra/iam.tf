locals {
  lambda_execution_role_name   = "${var.env}-mgmt-iamrole-${var.project_code}"
  lambda_execution_policy_name = "${var.env}-mgmt-iampolicy-${var.project_code}"
}

module "lambda_execution_role" {
  source = "./modules/iam_role"
  name   = local.lambda_execution_role_name
  policy_attachments = [
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  ]
  # custom_policy = {
  #   name = local.lambda_execution_policy_name
  #   statements = {
  #     DynamodbPermissions = {
  #       effect = "Allow"
  #       actions = [
  #         "dynamodb:PutItem",
  #         "dynamodb:GetItem",
  #         "dynamodb:UpdateItem",
  #       ]
  #       resources = [
  #         aws_dynamodb_table.table.arn
  #       ]
  #     }
  #   }
  # }
}

