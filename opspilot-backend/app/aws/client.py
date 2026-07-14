"""Thin boto3 client factory.

This is the ONLY place boto3.client() gets called. Services import from
here rather than constructing clients themselves, so there's exactly one
place to change if we ever add session caching, retries config, or a
role-assumption path.

Credentials (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) are intentionally
never touched here — boto3 resolves them from the environment on its own.
Only region is passed explicitly, so behavior doesn't depend on ambient
AWS_DEFAULT_REGION/AWS_REGION env var quirks.

Thread safety of client *creation* (not of using an already-created
client -- boto3/botocore clients are documented as safe for concurrent API
calls once constructed): this module has always had a single
`@lru_cache`d `_session()`, and until the region-scan parallelization fix
(see scan_service.py's `_run_collectors_concurrently`) every call into this
file ran on one thread at a time, so it never mattered in practice.  That
is no longer true -- `_run_scan`/`list_lite_resources` now run several of
these `get_*_client()` functions concurrently, from a `ThreadPoolExecutor`,
against this same shared `Session`, for the first time in this codebase's
history.

`boto3.Session.client()` is NOT documented as safe to call concurrently
from multiple threads for *first-time* construction of a given
service/region client pair: botocore's client creation path loads and
caches service/endpoint JSON models through a shared, per-session data
loader, and neither boto3 nor botocore's docs make any concurrency
guarantee about that loader for concurrent first-time loads (only
`Session` objects are explicitly called out at all, and only as "not
guaranteed thread-safe" -- see the long-standing boto3 FAQ guidance to
either give each thread its own `Session` or serialize client creation).
Since a class of failures there (corrupted/partial cache entries, or two
threads racing to populate the same loader cache key) would be silent and
intermittent rather than a clean exception, this takes the conservative
option explicitly allowed by the guardrails: a small lock around client
*construction* only, not around the actual AWS API call an already-built
client goes on to make (that would defeat the point of parallelizing).
Kept as one dedicated lock here rather than each service module rolling
its own, for the same "one place to change" reason `_session()` itself is
centralized -- and kept independent of every lock in scan_service.py
(`_region_locks_guard`, `_in_flight_scans_guard`, `_valid_regions_guard`)
so it can never be nested with them.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

import boto3

from app.core.config import get_settings


@lru_cache
def _session() -> boto3.Session:
    return boto3.Session(region_name=get_settings().aws_region)


# Guards boto3 client *construction* only (`_session().client(...)`) --
# see this module's docstring for why. Never held across an actual AWS API
# call, and never acquired anywhere that also holds one of
# scan_service.py's locks.
_client_creation_lock = threading.Lock()


def _client(service_name: str, region: str | None = None) -> Any:
    """Single choke point every get_*_client() below funnels through --
    same "one place to change" rationale as `_session()` itself, now also
    the one place enforcing the client-creation lock (see module
    docstring) rather than each function repeating its own
    lock/if-region/else dance.
    """
    with _client_creation_lock:
        if region:
            return _session().client(service_name, region_name=region)
        return _session().client(service_name)


def get_ec2_client(region: str | None = None) -> Any:
    """`region` overrides the account's configured region for this one
    client -- needed for region-wide scanning (roadmap 3.3), where the
    same service module is asked to list resources in a region other than
    the process-wide default. None/omitted behaves exactly as before.
    """
    return _client("ec2", region)


def get_cloudwatch_client(region: str | None = None) -> Any:
    """`region` overrides the account's configured region for this one
    client -- needed for CloudFront, whose CloudWatch metrics are only
    ever published to us-east-1 regardless of where the distribution's
    edge locations are or what region the rest of the app is scanning
    (roadmap Step 3 batch B note). Every other caller omits `region` and
    gets the normal session-configured client, unchanged from before.
    """
    return _client("cloudwatch", region)


def get_lambda_client(region: str | None = None) -> Any:
    return _client("lambda", region)


def get_s3_client() -> Any:
    return _client("s3")


def get_dynamodb_client(region: str | None = None) -> Any:
    return _client("dynamodb", region)


def get_sns_client() -> Any:
    return _client("sns")


def get_cloudtrail_client() -> Any:
    return _client("cloudtrail")


def get_rds_client(region: str | None = None) -> Any:
    return _client("rds", region)


def get_elbv2_client(region: str | None = None) -> Any:
    """Modern (Application/Network Load Balancer) client -- primary ELB
    target per roadmap Section 3.1 Step 3. See get_elb_client() below for
    the lower-priority Classic Load Balancer client.
    """
    return _client("elbv2", region)


def get_elb_client(region: str | None = None) -> Any:
    """Classic Load Balancer client. Lower priority per roadmap (Step 3
    "already-scoped types") -- implemented anyway since it's a small
    addition on top of the elbv2 path in elb_service.py, not skipped.
    """
    return _client("elb", region)


def get_elasticache_client(region: str | None = None) -> Any:
    return _client("elasticache", region)


def get_sagemaker_client(region: str | None = None) -> Any:
    return _client("sagemaker", region)


def get_redshift_client(region: str | None = None) -> Any:
    return _client("redshift", region)


def get_apigateway_client(region: str | None = None) -> Any:
    """REST APIs (API Gateway v1) -- see api_gateway_service.py's module
    docstring for why REST (not HTTP/apigatewayv2) is the targeted API type
    for this build step.
    """
    return _client("apigateway", region)


def get_cloudfront_client() -> Any:
    """CloudFront is a global service with a single API endpoint -- boto3
    resolves it correctly regardless of session region, so no region
    override is needed here (unlike get_cloudwatch_client(region=...)
    above, which IS needed because CloudFront's *metrics*, as opposed to
    its management API, are us-east-1-only).
    """
    return _client("cloudfront")


def get_opensearch_client(region: str | None = None) -> Any:
    return _client("opensearch", region)


def get_kinesis_client(region: str | None = None) -> Any:
    return _client("kinesis", region)


def get_sts_client() -> Any:
    """STS -- used only by app/services/account_service.py to resolve the
    connected AWS account ID for the Settings tab's "connected account"
    display (roadmap Section 5). Deliberately not region-overridable like
    most other clients here: STS's global endpoint resolves correctly
    regardless of session region, and this app only ever wants "the
    account currently configured," never a per-scan-region identity.
    """
    return _client("sts")


def get_pricing_client() -> Any:
    """The AWS Pricing API (`pricing`) only has endpoints in us-east-1,
    ap-south-1, and eu-central-1 -- unlike every other client in this file,
    it is deliberately hardcoded to us-east-1 regardless of
    get_settings().aws_region. This is a query endpoint for published price
    lists, not a regional resource -- it can price any region's products
    from any of those three endpoints.
    """
    with _client_creation_lock:
        return _session().client("pricing", region_name="us-east-1")
