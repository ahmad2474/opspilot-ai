from __future__ import annotations

from app.aws.client import get_dynamodb_client
from app.models.dashboard import DynamoCard, DynamoTableSummary


def list_tables() -> DynamoCard:
    client = get_dynamodb_client()
    paginator = client.get_paginator("list_tables")
    table_names: list[str] = []
    for page in paginator.paginate():
        table_names.extend(page.get("TableNames", []))

    tables: list[DynamoTableSummary] = []
    for name in table_names:
        detail = client.describe_table(TableName=name)["Table"]
        tables.append(
            DynamoTableSummary(
                name=name,
                status=detail.get("TableStatus", "unknown"),
                item_count=detail.get("ItemCount"),
            )
        )
    return DynamoCard(tables=tables, count=len(tables))
