variable "aws_region" {
  description = "AWS region to provision the DynamoDB tables in. Must match AWS_REGION in opspilot-backend/.env -- DynamoDB is regional, and the app reads/writes these tables in whichever region it's configured for."
  type        = string
  default     = "us-east-1"
}

# Defaults match app/core/config.py's Settings defaults exactly. Override
# only if you've also overridden the corresponding OPSPILOT_*_TABLE env
# var in opspilot-backend/.env -- the names must match on both sides or
# the app will look for tables that don't exist.
variable "investigations_table_name" {
  description = "Must match OPSPILOT_INVESTIGATIONS_TABLE (opspilot_investigations_table in Settings)."
  type        = string
  default     = "opspilot-investigations"
}

variable "mcp_tokens_table_name" {
  description = "Must match OPSPILOT_MCP_TOKENS_TABLE (opspilot_mcp_tokens_table in Settings)."
  type        = string
  default     = "opspilot-mcp-tokens"
}

variable "audit_log_table_name" {
  description = "Must match OPSPILOT_AUDIT_LOG_TABLE (opspilot_audit_log_table in Settings)."
  type        = string
  default     = "opspilot-audit-log"
}

variable "tags" {
  description = "Optional tags applied to all 3 tables."
  type        = map(string)
  default     = { Project = "opspilot-ai" }
}
