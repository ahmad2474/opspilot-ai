"""Shared translation from a caught AWS/botocore exception into a safe,
sanitized (status_code, detail) HTTP response.

Fixes the class of bug where a raw botocore.exceptions.ClientError message
was allowed to propagate straight into an HTTP response body -- that
message routinely embeds the full IAM caller ARN, including the 12-digit
AWS account ID (see app/services/scan_service.py's ScanFailedNoCacheError
handling in app/api/routes/resources.py, and that route's
list_available_regions, for the established rationale/precedent this
module follows).

Mirrors app/core/security.py: a small, focused app/core module with no
AWS calls of its own (unlike app/services/*, which does the actual boto3
work) -- this only translates an exception a route already caught.

Convention (matching list_available_regions / ScanFailedNoCacheError's
existing handling in app/api/routes/resources.py): every AWS-call failure
converted to an HTTP response here uses status 502, never a 503 -- this
app has no separate "AWS temporarily unavailable" status convention, and
502 ("this backend depends on an upstream that failed") is the one
already established. The real exception is logged server-side
(exc_info=True) by this helper before returning -- never included in the
response `detail` (no str(exc), no exception attribute, ever).
"""
from __future__ import annotations

import logging

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException

# Every AWS-call failure this app converts to an HTTP response uses this
# status code -- see module docstring for why 502 (not 503) is the
# established convention here.
AWS_ERROR_STATUS_CODE = 502

# ClientError response["Error"]["Code"] values that mean "our own IAM
# credentials lack a permission" -- distinct from a resource genuinely not
# existing (ResourceNotFoundException, below) or a generic AWS failure.
_ACCESS_DENIED_CODES = {"AccessDeniedException", "AccessDenied", "UnauthorizedOperation"}
_RESOURCE_NOT_FOUND_CODES = {"ResourceNotFoundException"}

_ACCESS_DENIED_MESSAGE = (
    "AWS permission error -- the backend's IAM credentials don't have access to this resource."
)
_RESOURCE_NOT_FOUND_MESSAGE = (
    "AWS resource unavailable -- a required table/resource may not exist yet."
)
_UNREACHABLE_MESSAGE = "AWS unreachable -- the backend couldn't reach AWS."
_GENERIC_MESSAGE = "AWS request failed."


def _detail_for(exc: Exception) -> str:
    """Maps a caught exception to one of the fixed, sanitized detail
    strings above. Never includes str(exc) or any attribute of the caught
    exception in the returned value -- only the exception's *category*
    (a ClientError's service-reported Error.Code, or the exception's own
    class for anything else) drives which fixed string comes back. See
    module docstring.
    """
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _ACCESS_DENIED_CODES:
            return _ACCESS_DENIED_MESSAGE
        if code in _RESOURCE_NOT_FOUND_CODES:
            return _RESOURCE_NOT_FOUND_MESSAGE
        return _GENERIC_MESSAGE
    if isinstance(exc, BotoCoreError):
        # Covers EndpointConnectionError, ConnectTimeoutError, and every
        # other non-ClientError botocore failure (a request that never
        # reached AWS at all: DNS/connection/timeout issues) -- none of
        # these carry a service-side Error.Code, so there's nothing
        # further to subdivide; all of them mean "AWS wasn't reachable".
        return _UNREACHABLE_MESSAGE
    return _GENERIC_MESSAGE


def aws_error_to_http_exception(
    exc: Exception, *, logger: logging.Logger, context: str
) -> HTTPException:
    """Logs the real exception server-side (exc_info=True, matching
    scan_service._do_scan's existing logging style) and returns an
    HTTPException carrying only a sanitized, fixed detail string -- never
    str(exc) -- for the route to `raise ... from exc`.

    `context` is a short human label (e.g. "get_audit_log") included only
    in the *server-side* log line, never in the response body -- it never
    reaches the caller.
    """
    logger.warning("%s failed", context, exc_info=True)
    return HTTPException(status_code=AWS_ERROR_STATUS_CODE, detail=_detail_for(exc))
