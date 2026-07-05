from __future__ import annotations

from app.aws.client import get_lambda_client
from app.models.dashboard import LambdaCard, LambdaFunctionSummary


def list_functions() -> LambdaCard:
    client = get_lambda_client()
    paginator = client.get_paginator("list_functions")
    functions: list[LambdaFunctionSummary] = []
    for page in paginator.paginate():
        for raw in page.get("Functions", []):
            functions.append(
                LambdaFunctionSummary(
                    name=raw["FunctionName"],
                    runtime=raw.get("Runtime"),
                    last_modified=raw.get("LastModified"),
                )
            )
    return LambdaCard(functions=functions, count=len(functions))
