output "ecr_repository_url" {
  value = aws_ecr_repository.this.repository_url
}

# Space-separated so the deploy workflows can loop over it from `terraform output -raw`.
output "function_names" {
  value = join(" ", [
    module.receiver_function.function.function_name,
    module.processor_function.function.function_name,
    module.notifier_function.function.function_name,
  ])
}

output "api_gateway_invoke_url" {
  value = module.apigw.api.api_endpoint
}
