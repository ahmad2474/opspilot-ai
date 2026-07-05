"""Dashboard breadth endpoint (Phase 4).

One combined call for Lambda/S3/DynamoDB/SNS/RDS/CloudTrail — visual
status cards only, no agent reasoning. EC2/CloudWatch stay on their own
/resources/ec2 endpoint since that one's the deep investigation surface.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.models.dashboard import DashboardOverview
from app.services import cloudtrail_service, dynamodb_service, lambda_service, rds_service, s3_service, sns_service

logger = logging.getLogger("app.api.dashboard")

router = APIRouter()


@router.get("/resources/overview", response_model=DashboardOverview)
async def get_overview() -> DashboardOverview:
    overview = DashboardOverview(
        lambda_functions=lambda_service.list_functions(),
        s3=s3_service.list_buckets(),
        dynamodb=dynamodb_service.list_tables(),
        sns=sns_service.list_topics(),
        rds=rds_service.list_instances(),
        cloudtrail=cloudtrail_service.list_recent_management_events(),
    )
    logger.info(
        "resources_overview lambda=%d s3=%d dynamodb=%d sns=%d rds=%d cloudtrail=%d",
        overview.lambda_functions.count,
        overview.s3.count,
        overview.dynamodb.count,
        overview.sns.count,
        overview.rds.count,
        len(overview.cloudtrail.events),
    )
    return overview
