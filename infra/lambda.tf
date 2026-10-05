locals {
  receiver_function_name  = "${var.env}-app-func-${var.project_code}-receiver"
  processor_function_name = "${var.env}-app-func-${var.project_code}-processor"
  notifier_function_name  = "${var.env}-app-func-${var.project_code}-notifier"
}

# All three functions run the same image (see Dockerfile) with different handlers.

module "receiver_function" {
  source             = "./modules/lambda_function"
  function_name      = local.receiver_function_name
  execution_role_arn = module.lambda_execution_role.role.arn
  deployment_package = {
    image_uri = "${aws_ecr_repository.this.repository_url}:placeholder"
  }
  image_config = {
    command = ["receiver.lambda_function.lambda_handler"]
  }
  ignore_deployment_package_changes = true

  depends_on = [null_resource.push_placeholder_image]

  timeout     = 10
  memory_size = 256

  environment_variables = {
    PROCESSOR_FUNCTION_NAME = module.processor_function.function.function_name
  }
}

module "processor_function" {
  source             = "./modules/lambda_function"
  function_name      = local.processor_function_name
  execution_role_arn = module.lambda_execution_role.role.arn
  deployment_package = {
    image_uri = "${aws_ecr_repository.this.repository_url}:placeholder"
  }
  image_config = {
    command = ["processor.lambda_function.lambda_handler"]
  }
  ignore_deployment_package_changes = true

  depends_on = [null_resource.push_placeholder_image]

  timeout     = 30
  memory_size = 512

  environment_variables = {
    TELEGRAM_BOT_TOKEN    = var.telegram_bot_token
    USERS_TABLE_NAME      = aws_dynamodb_table.users.name
    LAUNDRY_TABLE_NAME    = aws_dynamodb_table.laundry_machines.name
    NOTIFIER_FUNCTION_ARN = module.notifier_function.function.arn
    SCHEDULER_ROLE_ARN    = module.scheduler_execution_role.role.arn
  }
}

module "notifier_function" {
  source             = "./modules/lambda_function"
  function_name      = local.notifier_function_name
  execution_role_arn = module.lambda_execution_role.role.arn
  deployment_package = {
    image_uri = "${aws_ecr_repository.this.repository_url}:placeholder"
  }
  image_config = {
    command = ["notifier.lambda_function.lambda_handler"]
  }
  ignore_deployment_package_changes = true

  depends_on = [null_resource.push_placeholder_image]

  timeout     = 10
  memory_size = 256

  environment_variables = {
    TELEGRAM_BOT_TOKEN = var.telegram_bot_token
  }
}
