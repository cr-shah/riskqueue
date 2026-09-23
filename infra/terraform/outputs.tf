output "raw_events_bucket" {
  description = "Immutable raw-event landing bucket."
  value       = aws_s3_bucket.raw_events.id
}

output "processing_queue_url" {
  description = "Worker processing queue URL."
  value       = aws_sqs_queue.processing.url
}

output "dead_letter_queue_url" {
  description = "Failed processing message queue URL."
  value       = aws_sqs_queue.dead_letter.url
}

output "ingestion_lambda_name" {
  description = "S3 event ingestion Lambda function."
  value       = aws_lambda_function.ingestion.function_name
}

output "worker_iam_policy_arn" {
  description = "IAM policy to attach to the container worker task role."
  value       = aws_iam_policy.worker.arn
}
