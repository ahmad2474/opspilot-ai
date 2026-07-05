"""Thin boto3 client factory.

This is the ONLY place boto3.client() gets called. Services import from
here rather than constructing clients themselves, so there's exactly one
place to change if we ever add session caching, retries config, or a
role-assumption path.

Credentials (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) are intentionally
never touched here — boto3 resolves them from the environment on its own.
Only region is passed explicitly, so behavior doesn't depend on ambient
AWS_DEFAULT_REGION/AWS_REGION env var quirks.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3

from app.core.config import get_settings


@lru_cache
def _session() -> boto3.Session:
    return boto3.Session(region_name=get_settings().aws_region)


def get_ec2_client() -> Any:
    return _session().client("ec2")


def get_cloudwatch_client() -> Any:
    return _session().client("cloudwatch")


def get_lambda_client() -> Any:
    return _session().client("lambda")


def get_s3_client() -> Any:
    return _session().client("s3")


def get_dynamodb_client() -> Any:
    return _session().client("dynamodb")


def get_sns_client() -> Any:
    return _session().client("sns")


def get_cloudtrail_client() -> Any:
    return _session().client("cloudtrail")


def get_rds_client() -> Any:
    return _session().client("rds")
