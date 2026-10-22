
locals {
  lambda_execution_role_name   = "${var.env}-mgmt-iamrole-${var.project_code}"
  lambda_execution_policy_name = "${var.env}-mgmt-iampolicy-${var.project_code}"
}

data "aws_caller_identity" "current" {}

# Execution role for the bot function.
module "lambda_execution_role" {
  source = "./modules/iam_role"
  name   = local.lambda_execution_role_name

  policy_attachments = [
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  ]

  custom_policy = {
    name = local.lambda_execution_policy_name

    statements = {

      # Existing permissions for DynamoDB operations.
      DynamodbPermissions = {
        effect = "Allow"
        actions = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
        ]
        resources = [
          aws_dynamodb_table.users.arn,
          aws_dynamodb_table.laundry_machines.arn,
        ]
      }
      # NEW: Allow deletion of consumed authentication tokens.
      # Restricted to the users table.
      AuthTokenDeletion = {
        effect = "Allow"
        actions = [
          "dynamodb:DeleteItem",
        ]
        resources = [
          aws_dynamodb_table.users.arn,
        ]
      }

      TextractPermissions = {
        effect    = "Allow"
        actions   = ["textract:AnalyzeExpense"]
        resources = ["*"]
      }

      # Permissions to create laundry and bill reminder schedules.
      SchedulerPermissions = {
        effect  = "Allow"
        actions = ["scheduler:CreateSchedule"]
        resources = [
          "arn:aws:scheduler:${var.region}:${data.aws_caller_identity.current.account_id}:schedule/default/Laundry_*",
          "arn:aws:scheduler:${var.region}:${data.aws_caller_identity.current.account_id}:schedule/default/Split_*",
        ]
      }
      BillReminderCancellation = {
        effect  = "Allow"
        actions = ["scheduler:DeleteSchedule"]
        resources = [
          "arn:aws:scheduler:${var.region}:${data.aws_caller_identity.current.account_id}:schedule/default/Split_*",
        ]
      }

      # Allow passing the scheduler execution role to EventBridge.
      PassSchedulerRole = {
        effect    = "Allow"
        actions   = ["iam:PassRole"]
        resources = [module.scheduler_execution_role.role.arn]
      }
    }
  }
}
