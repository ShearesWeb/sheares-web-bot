locals {
  bot_function_name = "${var.env}-app-func-${var.project_code}-bot"
  # Built from the name: the function's env and the scheduler role both need this ARN,
  # and referencing module.bot_function from either is a dependency cycle.
  bot_function_arn = "arn:aws:lambda:${var.region}:${data.aws_caller_identity.current.account_id}:function:${local.bot_function_name}"
}

module "bot_function" {
  source             = "./modules/lambda_function"
  function_name      = local.bot_function_name
  execution_role_arn = module.lambda_execution_role.role.arn
  deployment_package = {
    image_uri = "${aws_ecr_repository.this.repository_url}:placeholder"
  }
  image_config = {
    command = ["app.lambda_handler"]
  }
  ignore_deployment_package_changes = true

  depends_on = [null_resource.push_placeholder_image]

  timeout     = 60
  memory_size = 512

  environment_variables = {
    TELEGRAM_BOT_TOKEN  = var.telegram_bot_token
    USERS_TABLE_NAME    = aws_dynamodb_table.users.name
    LAUNDRY_TABLE_NAME  = aws_dynamodb_table.laundry_machines.name
    SCHEDULER_ROLE_ARN  = module.scheduler_execution_role.role.arn
    SCHEDULE_TARGET_ARN = local.bot_function_arn
  }
}
