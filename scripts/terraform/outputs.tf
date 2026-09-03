output "investigations_table_name" {
  value = aws_dynamodb_table.investigations.name
}

output "mcp_tokens_table_name" {
  value = aws_dynamodb_table.mcp_tokens.name
}

output "audit_log_table_name" {
  value = aws_dynamodb_table.audit_log.name
}

output "table_arns" {
  description = "All 3 table ARNs, for reference if you want to scope an IAM policy to these specific resources rather than the account-wide dynamodb:* pattern docs/iam-policy.json uses today."
  value = [
    aws_dynamodb_table.investigations.arn,
    aws_dynamodb_table.mcp_tokens.arn,
    aws_dynamodb_table.audit_log.arn,
  ]
}
