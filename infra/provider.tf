provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Environment = var.env
      ProjectCode = var.project_code
      Terraform   = "true"
    }
  }
}
