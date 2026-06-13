terraform {
  required_version = ">=1.4.2"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.4.0"
    }
  }

  backend "s3" {
    bucket               = "com-ops-bucket-tfstate-shweb-759655304761-ap-southeast-1-an"
    key                  = "sheareswebbot.tfstate"
    workspace_key_prefix = "tf-state"
    region               = "ap-southeast-1"
  }

}
