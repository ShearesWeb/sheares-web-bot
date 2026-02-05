locals {
  ecr_repository_name = "${var.env}-app-ecrrepo-${var.project_code}"
}

resource "aws_ecr_repository" "this" {
  name                 = local.ecr_repository_name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  lifecycle {
    ignore_changes = [
      image_tag_mutability,
      force_delete,
    ]
  }

}

resource "null_resource" "push_placeholder_image" {
  provisioner "local-exec" {
    command = <<EOT
      set -e

      echo "🔧 Logging in to ECR..."
      aws ecr get-login-password --region ${var.region} \
        | docker login --username AWS --password-stdin ${aws_ecr_repository.this.repository_url}

      echo "📦 Writing minimal Dockerfile..."
      mkdir -p .terraform-tmp/placeholder
      cat > .terraform-tmp/placeholder/Dockerfile <<EOF
FROM alpine:3.19
LABEL maintainer="terraform-bootstrap"
CMD ["echo", "placeholder image for Lambda"]
EOF

      echo "🐳 Building and pushing placeholder image..."
      docker build -t ${aws_ecr_repository.this.repository_url}:placeholder .terraform-tmp/placeholder
      docker push ${aws_ecr_repository.this.repository_url}:placeholder

      echo "🧹 Cleaning up..."
      rm -rf .terraform-tmp/
    EOT
  }

  depends_on = [aws_ecr_repository.this]
}
