locals {
  function_name = "${var.env}-app-func-${var.project_code}"
}

module "lambda_function" {
  source             = "./modules/lambda_function"
  function_name      = local.function_name
  execution_role_arn = module.lambda_execution_role.role.arn
  deployment_package = {
    image_uri = "${aws_ecr_repository.this.repository_url}:placeholder"
  }
  ignore_deployment_package_changes = true

  depends_on = [null_resource.push_placeholder_image]

  timeout                = 30
  memory_size            = 1024
  ephemeral_storage_size = 512

  environment_variables = {
    # DYNAMODB_TABLE_NAME = aws_dynamodb_table.table.name
    TELEGRAM_BOT_TOKEN = var.telegram_bot_token
  }
}
