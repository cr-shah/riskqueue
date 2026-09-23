variable "aws_region" {
  description = "AWS region for RiskQueue ingestion resources."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Lowercase name prefix used for resources."
  type        = string
  default     = "riskqueue"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.project_name))
    error_message = "project_name must be a lowercase AWS-compatible prefix."
  }
}

variable "environment" {
  description = "Deployment environment suffix."
  type        = string
  default     = "dev"
}

variable "lambda_package_path" {
  description = "Path to the prebuilt Lambda deployment zip."
  type        = string
  default     = "../../dist/riskqueue-ingestion-lambda.zip"
}

variable "message_retention_seconds" {
  description = "Retention period for processing and dead-letter messages."
  type        = number
  default     = 1209600
}

variable "max_receive_count" {
  description = "Failed receives before SQS moves a message to the dead-letter queue."
  type        = number
  default     = 5
}
