output "ecr_repository_url" {
  value = aws_ecr_repository.this.repository_url
}

output "function_name" {
  value = module.lambda_function.function.function_name
}

output "api_gateway_invoke_url" {
  value = module.apigw.api.api_endpoint
}