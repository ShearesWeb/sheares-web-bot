terraform {
  required_version = ">=1.4.2"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.4.0"
    }
  }

  backend "s3" {
    bucket               = "com-all-bucket-tfstate-shearesweb"
    key                  = "sheareswebbot.tfstate"
    workspace_key_prefix = "tf-state"
    region               = "ap-southeast-1"
  }

}
