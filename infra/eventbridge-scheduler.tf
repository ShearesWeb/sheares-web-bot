# locals {
#   schedule_name                  = "${var.env}-app-schedule-${var.project_code}"
#   schedule_group_name            = "${var.env}-app-schedulegrp-${var.project_code}"
#   schedule_execution_role_name   = "${var.env}-mgmt-iamrole-${var.project_code}-scheduler"
#   schedule_execution_policy_name = "${var.env}-mgmt-iampolicy-${var.project_code}-scheduler"
# }


# resource "aws_scheduler_schedule_group" "scheduler" {
#   name = local.schedule_group_name
# }

# resource "aws_scheduler_schedule" "scheduler" {
#   name       = local.schedule_name
#   group_name = aws_scheduler_schedule_group.scheduler.name

#   flexible_time_window {
#     mode = "OFF"
#   }

#   schedule_expression          = "cron(0/30 * * * ? *)"
#   schedule_expression_timezone = "Asia/Singapore"

#   target {
#     arn      = module.lambda_function.function.arn
#     role_arn = module.scheduler_execution_role.role.arn

#     retry_policy {
#       maximum_event_age_in_seconds = 300
#       maximum_retry_attempts       = 3
#     }
#   }
# }

# module "scheduler_execution_role" {
#   source = "./modules/iam_role"
#   name   = local.schedule_execution_role_name
#   assume_role_allowed_principals = [{
#     type        = "Service"
#     identifiers = ["scheduler.amazonaws.com"]
#   }]
#   custom_policy = {
#     name = local.schedule_execution_policy_name
#     statements = {
#       lambdaPermissions = {
#         effect = "Allow"
#         actions = [
#           "lambda:InvokeFunction",
#         ]
#         resources = [module.lambda_function.function.arn]
#       }
#     }
#   }
# }
