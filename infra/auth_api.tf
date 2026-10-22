locals {
  auth_function_name               = "${var.env}-app-func-${var.project_code}-auth"
  auth_api_name                    = "${var.env}-internal-apigw-${var.project_code}-auth"
  auth_execution_role_name         = "${var.env}-auth-iamrole-${var.project_code}"
  auth_execution_policy_name       = "${var.env}-auth-iampolicy-${var.project_code}"
  intranet_link_caller_user_name   = "${var.env}-intranet-${var.project_code}-link-caller"
  intranet_link_caller_policy_name = "${var.env}-intranet-${var.project_code}-link-invoke"
}

# Account-linking operations deliberately use a separate API. This keeps them
# out of the bot API's public ANY /{proxy+} route and lets API Gateway reject
# unsigned calls before the auth Lambda runs.
resource "aws_apigatewayv2_api" "auth" {
  name          = local.auth_api_name
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "auth_default" {
  api_id      = aws_apigatewayv2_api.auth.id
  name        = "$default"
  auto_deploy = true
}

module "auth_lambda_execution_role" {
  source = "./modules/iam_role"
  name   = local.auth_execution_role_name

  policy_attachments = [
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  ]

  custom_policy = {
    name = local.auth_execution_policy_name
    statements = {
      LinkTableAccess = {
        effect = "Allow"
        actions = [
          "dynamodb:GetItem",
          "dynamodb:TransactWriteItems",
        ]
        resources = [aws_dynamodb_table.users.arn]
      }
    }
  }
}

module "auth_function" {
  source             = "./modules/lambda_function"
  function_name      = local.auth_function_name
  execution_role_arn = module.auth_lambda_execution_role.role.arn
  deployment_package = {
    image_uri = "${aws_ecr_repository.this.repository_url}:placeholder"
  }
  image_config = {
    command = ["auth_app.lambda_handler"]
  }
  ignore_deployment_package_changes = true

  depends_on = [null_resource.push_placeholder_image]

  timeout     = 10
  memory_size = 256

  environment_variables = {
    USERS_TABLE_NAME = aws_dynamodb_table.users.name
  }
}

resource "aws_apigatewayv2_integration" "auth" {
  api_id                 = aws_apigatewayv2_api.auth.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.auth_function.function.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "auth_preview" {
  api_id             = aws_apigatewayv2_api.auth.id
  route_key          = "POST /internal/telegram-link/preview"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.auth.id}"
}

resource "aws_apigatewayv2_route" "auth_complete" {
  api_id             = aws_apigatewayv2_api.auth.id
  route_key          = "POST /internal/telegram-link/complete"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.auth.id}"
}

resource "aws_lambda_permission" "auth_api" {
  statement_id  = "AllowExecutionFromAuthApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = module.auth_function.function.function_name
  principal     = "apigateway.amazonaws.com"

  # Do not grant the API permission to invoke arbitrary Lambda routes.
  source_arn = "${aws_apigatewayv2_api.auth.execution_arn}/*/POST/internal/telegram-link/*"
}

# Terraform provisions the narrowly scoped identity and policy, but deliberately
# does not create an access key. Operations creates and rotates that credential
# out-of-band, storing it only as encrypted Cloudflare Worker secrets.
resource "aws_iam_user" "intranet_link_caller" {
  name = local.intranet_link_caller_user_name
}

data "aws_iam_policy_document" "intranet_link_caller" {
  statement {
    effect  = "Allow"
    actions = ["execute-api:Invoke"]
    resources = [
      "${aws_apigatewayv2_api.auth.execution_arn}/*/POST/internal/telegram-link/preview",
      "${aws_apigatewayv2_api.auth.execution_arn}/*/POST/internal/telegram-link/complete",
    ]
  }
}

resource "aws_iam_user_policy" "intranet_link_caller" {
  name   = local.intranet_link_caller_policy_name
  user   = aws_iam_user.intranet_link_caller.name
  policy = data.aws_iam_policy_document.intranet_link_caller.json
}
