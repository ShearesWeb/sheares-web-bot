locals {
  schedule_execution_role_name   = "${var.env}-mgmt-iamrole-${var.project_code}-scheduler"
  schedule_execution_policy_name = "${var.env}-mgmt-iampolicy-${var.project_code}-scheduler"
}

# Role EventBridge Scheduler assumes to invoke the bot when a laundry
# reminder fires. The schedules themselves are created at runtime by the bot.
module "scheduler_execution_role" {
  source = "./modules/iam_role"
  name   = local.schedule_execution_role_name
  assume_role_allowed_principals = [{
    type        = "Service"
    identifiers = ["scheduler.amazonaws.com"]
  }]
  custom_policy = {
    name = local.schedule_execution_policy_name
    statements = {
      lambdaPermissions = {
        effect = "Allow"
        actions = [
          "lambda:InvokeFunction",
        ]
        resources = [local.bot_function_arn]
      }
    }
  }
}
