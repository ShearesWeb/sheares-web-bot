variable "region" {
  description = "The region where resources will be deployed."
  default     = "ap-southeast-1"
}

variable "project_code" {
  description = "The code name of the project used for naming convention."
}

variable "env" {
  description = "The target environment to which the resources will be deployed."
}

variable "telegram_bot_token" {
  description = "The telegram bot token provided by botfather."
  type        = string
  sensitive   = true
}

variable "telegram_webhook_secret" {
  description = "Secret Telegram includes in its webhook request header."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{32,256}$", var.telegram_webhook_secret))
    error_message = "telegram_webhook_secret must be 32-256 characters using only A-Z, a-z, 0-9, underscore and hyphen."
  }
}

variable "intranet_base_url" {
  description = "HTTPS origin of the Sheares Intranet linking page."
  type        = string

  validation {
    condition     = can(regex("^https://[^/]+$", var.intranet_base_url))
    error_message = "intranet_base_url must be an HTTPS origin without a trailing slash."
  }
}
