output "ecr_repository_url" {
  value = aws_ecr_repository.this.repository_url
}

# The release workflows loop over this space-separated list; there is one function now.
output "function_names" {
  value = module.bot_function.function.function_name
}

output "api_gateway_invoke_url" {
  value = module.apigw.api.api_endpoint
}
