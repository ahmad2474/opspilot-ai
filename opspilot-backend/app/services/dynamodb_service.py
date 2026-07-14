from __future__ import annotations

from app.aws.client import get_dynamodb_client
from app.models.dashboard import DynamoCard, DynamoTableSummary


def _to_summary(name: str, detail: dict) -> DynamoTableSummary:
    """Shared DescribeTable-response -> DynamoTableSummary mapping, reused
    by both list_tables() and get_table() so the two never drift."""
    billing_mode = detail.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED")
    throughput = detail.get("ProvisionedThroughput", {})
    return DynamoTableSummary(
        name=name,
        status=detail.get("TableStatus", "unknown"),
        item_count=detail.get("ItemCount"),
        creation_date_time=detail.get("CreationDateTime"),
        billing_mode=billing_mode,
        read_capacity_units=throughput.get("ReadCapacityUnits", 0) or 0,
        write_capacity_units=throughput.get("WriteCapacityUnits", 0) or 0,
    )


def list_tables(region: str | None = None) -> DynamoCard:
    client = get_dynamodb_client(region=region)
    paginator = client.get_paginator("list_tables")
    table_names: list[str] = []
    for page in paginator.paginate():
        table_names.extend(page.get("TableNames", []))

    tables: list[DynamoTableSummary] = []
    for name in table_names:
        detail = client.describe_table(TableName=name)["Table"]
        tables.append(_to_summary(name, detail))
    return DynamoCard(tables=tables, count=len(tables))


def get_table(table_name: str, region: str | None = None) -> DynamoTableSummary | None:
    """List-then-filter, same convention every other service module uses
    for its get_*() -- see lambda_service.get_function's docstring."""
    result = list_tables(region=region)
    for table in result.tables:
        if table.name == table_name:
            return table
    return None
