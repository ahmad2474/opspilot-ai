# DynamoDB provisioning -- Path B (docs/opspilot-ai-roadmap-phase2.md
# Section 4.3): an alternative to scripts/provision_tables.py for anyone
# who wants a self-contained DevOps artifact instead of a Python script.
# Provisions the exact same 3 tables, same schema, same billing mode --
# not two different designs, just two ways to create one design. Neither
# path is required; use whichever fits your workflow.
#
# Schema/table names are not guesses -- grepped directly from the real
# put_item/get_item/Key= calls in
# opspilot-backend/app/services/{investigation,mcp_auth,audit_log}_service.py
# before writing this (same verification scripts/provision_tables.py's
# own docstring describes): a single String partition key named "id" on
# every table, on-demand (PAY_PER_REQUEST) billing -- no capacity
# planning needed at this app's single-admin scale.
#
# This module only provisions the DynamoDB tables. It deliberately does
# NOT create the IAM user/policy (scripts/setup.py's boto3 path or the
# manual console click-through both already cover that, and duplicating
# it a third time in Terraform isn't worth the maintenance burden for a
# single-admin app) or touch anything else in the account.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

resource "aws_dynamodb_table" "investigations" {
  name         = var.investigations_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  tags = var.tags
}

resource "aws_dynamodb_table" "mcp_tokens" {
  name         = var.mcp_tokens_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  tags = var.tags
}

resource "aws_dynamodb_table" "audit_log" {
  name         = var.audit_log_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  tags = var.tags
}
