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
}
