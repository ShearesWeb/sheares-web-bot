output "ecr_repository_url" {
  value = aws_ecr_repository.this.repository_url
}

# The release workflows update both entrypoints with the same container image.
output "function_names" {
  value = join(" ", [
    module.bot_function.function.function_name,
    module.auth_function.function.function_name,
  ])
}

output "api_gateway_invoke_url" {
  value = module.apigw.api.api_endpoint
}

output "auth_api_invoke_url" {
  description = "Server-only account-linking API URL. Every route requires AWS SigV4 authentication."
  value       = aws_apigatewayv2_api.auth.api_endpoint
}

output "auth_api_invoke_arn" {
  description = "Scope for the intranet caller's execute-api:Invoke IAM policy."
  value       = "${aws_apigatewayv2_api.auth.execution_arn}/*/POST/internal/telegram-link/*"
}

output "intranet_link_caller_user_name" {
  description = "Dedicated IAM user for the Cloudflare server runtime. Its access key is managed outside Terraform."
  value       = aws_iam_user.intranet_link_caller.name
}
