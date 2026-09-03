#!/usr/bin/env python3
"""Provision the 3 DynamoDB tables this app needs -- Path A of the two
provisioning paths named in docs/opspilot-ai-roadmap-phase2.md Section
4.3 (Path B is the Terraform module in scripts/terraform/, an alternative
for anyone who wants a self-contained DevOps artifact instead).

Idempotent: safe to re-run. Table names, schema, and billing mode below
are not guesses -- they match exactly what
opspilot-backend/app/services/{investigation,mcp_auth,audit_log}_service.py
actually read/write (grepped their real put_item/get_item/Key= calls
before writing this, not inferred from the README's summary alone): a
single String partition key named "id" on every table, PAY_PER_REQUEST
billing (no capacity planning needed at this app's single-admin scale).

Run directly (`python3 scripts/provision_tables.py`) or via setup.py,
which calls this after writing AWS credentials to opspilot-backend/.env.
Reads AWS credentials/region the normal boto3 way (environment, or
opspilot-backend/.env if python-dotenv has already loaded it into the
environment by the time this runs -- setup.py guarantees that ordering;
a standalone run needs the credentials already in your shell environment).
"""
from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError

# Must match app/core/config.py's Settings defaults exactly
# (opspilot_investigations_table / opspilot_mcp_tokens_table /
# opspilot_audit_log_table) -- if you've overridden those via env vars,
# override the names below the same way before running this.
TABLE_NAMES = (
    "opspilot-investigations",
    "opspilot-mcp-tokens",
    "opspilot-audit-log",
)


def provision_table(client, table_name: str) -> None:
    try:
        client.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"Creating '{table_name}'...")
        client.get_waiter("table_exists").wait(TableName=table_name)
        print(f"'{table_name}' ready.")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceInUseException":
            print(f"'{table_name}' already exists -- leaving it as-is.")
        else:
            raise


def main() -> None:
    # Explicit region_name, not boto3's implicit env lookup -- this app's
    # own convention everywhere else (app/aws/client.py's
    # boto3.Session(region_name=get_settings().aws_region)) reads
    # AWS_REGION, but boto3 itself only recognizes AWS_DEFAULT_REGION
    # without an explicit region_name= argument. Live-verified: running
    # this with only AWS_REGION set (this app's actual .env convention)
    # raised botocore.exceptions.NoRegionError with no explicit region.
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    client = boto3.client("dynamodb", region_name=region)
    for name in TABLE_NAMES:
        provision_table(client, name)
    print("All 3 tables provisioned.")


if __name__ == "__main__":
    try:
        main()
    except ClientError as exc:
        print(f"AWS error while provisioning tables: {exc}", file=sys.stderr)
        print(
            "Confirm AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_REGION are set and the "
            "credentials have dynamodb:CreateTable/DescribeTable -- this is a one-time setup "
            "permission, not part of the app's own runtime IAM policy in docs/iam-policy.json "
            "(which is deliberately read-only forever).",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
