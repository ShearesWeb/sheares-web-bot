locals {
  apigw_name = "${var.env}-web-apigw-${var.project_code}"
}

module "apigw" {
  source = "./modules/apigw"
  name   = local.apigw_name
}


module "lambda_integration" {
  source                    = "./modules/apigw_lambda_integration"
  api_gateway_id            = module.apigw.api.id
  api_gateway_execution_arn = module.apigw.api.execution_arn
  function_name             = module.lambda_function.function.function_name
  function_invoke_arn       = module.lambda_function.function.invoke_arn
}