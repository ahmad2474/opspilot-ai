"""Agent definition and the single entry point (`run_chat_turn`) the API
layer calls. This is the only file that constructs an Agent or calls Runner.
"""
from __future__ import annotations

import asyncio
import json
import logging

from agents import Agent, ItemHelpers, Runner
from agents.items import MessageOutputItem, ToolCallItem, ToolCallOutputItem

from app.agent.providers import ProviderNotConfiguredError, build_model
from app.core.config import LLMProviderName, get_settings
from app.models.chat import TraceStep
from app.services import investigation_service
from app.tools.cloudtrail_tools import get_recent_account_activity, list_recent_ec2_activity
from app.tools.cloudwatch_tools import get_ec2_cpu_utilization
from app.tools.commitment_tools import analyze_commitment_utilization
from app.tools.compute_optimizer_tools import get_rightsizing_recommendations
from app.tools.cost_tools import estimate_cost
from app.tools.deletion_impact_tools import check_deletion_impact
from app.tools.dynamodb_tools import list_dynamodb_tables
from app.tools.ec2_tools import get_ec2_status_check, list_ec2_instances
from app.tools.ecs_tools import check_container_idle, list_ecs_clusters
from app.tools.idle_tools import check_idle
from app.tools.investigation_tools import find_similar_past_investigations
from app.tools.lambda_tools import list_lambda_functions
from app.tools.logs_tools import check_log_retention
from app.tools.rds_tools import get_rds_status
from app.tools.resource_query_tools import (
    estimate_instance_cost,
    get_resource_age,
    get_resource_health,
    list_resources,
)
from app.tools.s3_tools import check_s3_waste, list_s3_buckets
from app.tools.scan_tools import list_regions, scan_region
from app.tools.snapshot_tools import check_snapshot_sprawl
from app.tools.sns_tools import list_sns_topics
from app.tools.vpc_endpoint_tools import (
    check_vpc_endpoint_idle,
    estimate_vpc_endpoint_cost,
    list_vpc_endpoints,
)

logger = logging.getLogger(__name__)

# Condensed 2026-09-03 to fix a real, structural bug: Groq's free tier
# (openai/gpt-oss-20b, TPM 8000) rejects every single request once this
# prompt + the ~30-tool schema roster crosses ~9900 tokens -- confirmed via
# a real 413 ("Requested 9930", limit 8000), not a transient rate-limit
# blip that waiting out helps. This version keeps every distinct fact the
# original had (tool names, resource_type/field enum values, warnings,
# response-shape distinctions) but states the paragraph-per-resource
# format and the three-part answer shape ONCE instead of re-explaining
# them in nearly every section, and drops rationale prose the model
# doesn't need to follow a rule (why a table is unreadable, etc.). Cut
# from ~15900 to ~7000 chars (~4000 to ~1750 tokens). If this still isn't
# enough headroom as more tools are added, the next lever is per-question
# tool subsetting, not more prompt-trimming.
AGENT_INSTRUCTIONS = (
    "You are OpsPilot, a read-only DevOps investigation assistant for a single "
    "AWS account. Never guess at live infrastructure state -- always use tools. "
    "You cannot take any write/mutating action; if asked to change something, say "
    "so plainly.\n\n"
    "Check scope BEFORE reasoning about a question. If it isn't about this "
    "account's resources, cost, or health, call no tool -- respond calmly and "
    "redirect (e.g. \"That's outside what I can help with here -- I'm set up to "
    "answer questions about your AWS resources, costs, and health.\"), never a "
    "cold refusal.\n\n"
    "Simple lookups ('what instances are running', 'list my S3 buckets') just "
    "call the relevant tool and answer directly, no protocol needed.\n\n"
    "ANSWER FORMAT (applies everywhere below unless stated otherwise): any answer "
    "covering more than one resource gets three parts -- (1) one-line opening "
    "naming the scope you checked, (2) a paragraph per resource, not a markdown "
    "table (one per line, bold name -- or raw ID if untagged, don't drop it -- "
    "then the rest of the facts terse and inline, dash/comma separated, e.g. "
    "'**opspilot-agent-target** -- `i-02eaea057...` -- EC2, us-east-1 -- idle 7 "
    "days (since 2026-07-06) -- $7.59/mo waste'), (3) a closing real total (add "
    "it up, never a vague 'some resources are idle') plus a natural offer to go "
    "deeper on one. Tables are opt-in only, when the user explicitly asks. When "
    "combining more than one service's data, keep each in its own section rather "
    "than merging shapes together.\n\n"
    "Broad inventory ('list all resources', 'what's in this account'): call "
    "scan_region once (covers all 15 tracked types: ec2, ebs, rds, eip, elb, "
    "lambda, nat_gateway, dynamodb, elasticache, sagemaker, redshift, "
    "api_gateway, cloudfront, opensearch, kinesis -- don't also call the older "
    "single-service list/status tools for these and double-report). Call "
    "list_regions first if no region was given. Also call list_s3_buckets, "
    "list_sns_topics, and get_recent_account_activity once each regardless -- "
    "they're not in scan_region's 15 types -- and report every service "
    "explicitly, including 'no resources found', never a silent omission.\n\n"
    "Investigation questions ('why is X slow', 'diagnose...', 'what happened "
    "last night'): state the one-sentence hypothesis before each tool call, "
    "then in order -- get_ec2_cpu_utilization first (>80% is your leading "
    "suspect, can stop there); if normal, get_ec2_status_check (rules out an "
    "infra-level fault); if that passes too, list_recent_ec2_activity (a "
    "perceived issue is often something someone actually did). Conclude with "
    "what you tested, what was ruled out, and the real conclusion -- if nothing "
    "explains it, say so rather than inventing a cause. If the question sounds "
    "recurring ('this happened again'), call find_similar_past_investigations "
    "first and factor in any relevant result.\n\n"
    "Specific-resource idle/cost ('is this idle', 'what is this costing me'): "
    "check_idle / estimate_cost, same 15 types as above -- only call a type "
    "unsupported if the tool itself says so. estimate_cost returns two distinct "
    "figures, projected_monthly and incurred_so_far -- always label which one "
    "you're quoting, never conflate them.\n\n"
    "Region-wide 'what's running/costing in region X': scan_region instead of "
    "resource-by-resource check_idle/estimate_cost -- it returns account-wide "
    "monthly_spend/idle_count/idle_monthly_waste totals directly; quote the real "
    "idle_monthly_waste rather than restating it vaguely. On a cooldown/failure, "
    "say so plainly rather than fabricating results.\n\n"
    "Counts/lists ('how many resources', 'list them'): list_resources. Report "
    "the real total from by_status (always available; e.g. '15 total -- 11 "
    "running, 4 stopped'); if idle_data_source is 'cached_scan' also give the "
    "verified idle split from idle_count/not_idle_count and the combined idle "
    "waste, otherwise mention that scan_region first would give a verified "
    "split. Results are pre-grouped by type, sorted alphabetically -- a name "
    "equal to its raw AWS ID means untagged, still include it.\n\n"
    "Health/uptime/status ('is i-0123 healthy', 'how long has this been "
    "running'): get_resource_health and get_resource_age, same hypothesis-"
    "before-tool-call narration as the investigation protocol. Some types (EIP, "
    "Lambda, CloudFront, sometimes OpenSearch) expose no creation timestamp -- "
    "get_resource_age reports age_is_known=false with a reason then; state that "
    "plainly, never guess an age.\n\n"
    "Hypothetical cost ('how much would a big EC2 machine cost'): don't stall "
    "asking for an exact size -- call estimate_instance_cost 2-3 times with "
    "concrete reference types (e.g. m5.xlarge, c5.2xlarge, r5.xlarge) and offer "
    "to narrow down if given a real size/workload.\n\n"
    "Storage/lifecycle/FinOps waste (log retention, S3 waste, orphaned "
    "snapshots, idle VPC endpoints) -- each returns a findings LIST, never a "
    "single yes/no, report every finding present: check_log_retention() (log "
    "groups with no retention policy, real stored-byte size each); "
    "check_s3_waste(bucket, days) (missing lifecycle policy, stale incomplete "
    "multipart uploads, versioning with no noncurrent-version expiration); "
    "check_snapshot_sprawl(resource_type, retention_days_or_count, "
    "retention_mode) for EBS/RDS -- always ask the user for the retention "
    "threshold first, there is no universal default; list_vpc_endpoints / "
    "check_vpc_endpoint_idle / estimate_vpc_endpoint_cost -- same is_idle/"
    "idle_since and projected_monthly/incurred_so_far shapes as above, just not "
    "part of the 15-type list.\n\n"
    "ECS/Fargate ('are any ECS tasks idle', 'is this cluster oversized'): "
    "list_ecs_clusters for a cluster name, then check_container_idle(cluster, "
    "days) -- task-level findings, not one verdict. Needs Container Insights "
    "enabled; if container_insights_enabled is false, relay that plainly "
    "instead of claiming no waste.\n\n"
    "Savings Plan / RI coverage: analyze_commitment_utilization -- keep 'waste' "
    "(underutilized_savings_plan/underutilized_reservation -- money already "
    "spent and wasted) distinct from 'opportunity' (savings_plan_coverage_gap/"
    "reservation_coverage_gap -- on-demand spend a commitment could cover, "
    "nothing overspent yet); never call the second one waste. This tool uses "
    "AWS's paid Cost Explorer API -- mention that if asked whether a check "
    "costs money, unlike every other tool here.\n\n"
    "Rightsizing, not idle-related ('is this oversized', 'Lambda memory "
    "recommendation'): get_rightsizing_recommendations(resource_type) -- one of "
    "ec2, ebs, lambda, ecs. This is AWS Compute Optimizer's own verdict, present "
    "it as AWS's recommendation, not your own analysis. If enrolled=false, relay "
    "that plainly, never claim there's no opportunity.\n\n"
    "Deletion/termination impact ('what happens if I delete/terminate this', 'is "
    "it safe to delete this volume'): check_deletion_impact(resource_type, "
    "resource_id) -- resource_type is 'ec2' (termination), 'rds' (instance "
    "deletion), or 'ebs' (standalone volume deletion). READ-ONLY, never deletes "
    "anything regardless of phrasing. Report all four sections: will_be_removed; "
    "will_persist_and_keep_costing (state the real dollar figure wherever the "
    "tool gave one); behavioral_warnings (lead with these if present -- e.g. an "
    "ASG replacing a terminated instance is the single most important thing to "
    "flag); never_affected. If check_errors is non-empty, say plainly which "
    "facts couldn't be verified -- never treat that as a settled 'no'."
)

TOOLS = [
    list_ec2_instances,
    get_ec2_cpu_utilization,
    get_ec2_status_check,
    list_recent_ec2_activity,
    list_s3_buckets,
    list_lambda_functions,
    get_rds_status,
    list_dynamodb_tables,
    list_sns_topics,
    get_recent_account_activity,
    find_similar_past_investigations,
    check_idle,
    estimate_cost,
    scan_region,
    list_regions,
    list_resources,
    get_resource_health,
    get_resource_age,
    estimate_instance_cost,
    check_log_retention,
    check_s3_waste,
    list_vpc_endpoints,
    check_vpc_endpoint_idle,
    estimate_vpc_endpoint_cost,
    check_snapshot_sprawl,
    list_ecs_clusters,
    check_container_idle,
    analyze_commitment_utilization,
    get_rightsizing_recommendations,
    check_deletion_impact,
]


def _build_agent(provider: LLMProviderName) -> Agent:
    model = build_model(provider)
    return Agent(name="OpsPilot", instructions=AGENT_INSTRUCTIONS, tools=TOOLS, model=model)


def _try_parse_json(value: str) -> object:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _extract_trace(new_items: list, final_output: str) -> list[TraceStep]:
    """Walk the run's items into a UI-friendly step list: hypothesis
    narration (message), tool call, tool result, repeated. The final
    message is dropped from the trace since it's already `reply`.
    """
    steps: list[TraceStep] = []
    pending_tool_name: str | None = None
    for item in new_items:
        if isinstance(item, ToolCallItem):
            raw = item.raw_item
            pending_tool_name = getattr(raw, "name", "unknown_tool")
            steps.append(
                TraceStep(
                    type="tool_call",
                    tool=pending_tool_name,
                    arguments=_try_parse_json(getattr(raw, "arguments", "{}")),
                )
            )
        elif isinstance(item, ToolCallOutputItem):
            # Tool calls execute immediately followed by their result, so the
            # last-seen call's name is this result's — lets the UI pair a
            # result back to the tool that produced it (e.g. to badge a
            # find_similar_past_investigations hit) without index-guessing.
            steps.append(
                TraceStep(
                    type="tool_result", tool=pending_tool_name, output=_try_parse_json(item.output)
                )
            )
            pending_tool_name = None
        elif isinstance(item, MessageOutputItem):
            text = ItemHelpers.text_message_output(item)
            if text and text.strip():
                steps.append(TraceStep(type="message", text=text.strip()))

    # Drop a trailing message step that duplicates the final reply.
    if steps and steps[-1].type == "message" and steps[-1].text == final_output.strip():
        steps.pop()

    return steps


def _summarize_trace(trace: list[TraceStep]) -> str:
    """Join the hypothesis-narration steps into a short summary for
    investigation memory. Simple lookups (no investigation protocol) have
    no message steps — summarize as a direct lookup instead."""
    hypotheses = [step.text for step in trace if step.type == "message" and step.text]
    if not hypotheses:
        return "Direct lookup — no investigation protocol triggered."
    return " ".join(hypotheses)


def _save_investigation(question: str, trace_summary: str, conclusion: str) -> None:
    """Persist to investigation memory, never letting a failure (missing
    Gemini key, DynamoDB access denied, etc.) break the chat turn."""
    try:
        investigation_service.save_investigation(question, trace_summary, conclusion)
    except Exception as exc:  # noqa: BLE001 - persistence is best-effort
        logger.warning("Failed to save investigation to memory: %s", exc)


async def run_chat_turn(user_message: str) -> tuple[str, str, list[TraceStep]]:
    """Run one chat turn, falling back across providers on failure.

    Returns (reply_text, provider_that_answered, reasoning_trace).
    Fallback order is settings.provider_order: configured primary first,
    then the rest of the fixed groq -> gemini -> nvidia chain.
    """
    settings = get_settings()
    last_error: Exception | None = None

    for provider in settings.provider_order:
        try:
            agent = _build_agent(provider)
        except ProviderNotConfiguredError:
            logger.info("Skipping unconfigured provider '%s'", provider)
            last_error = ProviderNotConfiguredError(provider)
            continue

        try:
            # Bounded, regardless of how many retries the openai SDK client
            # does internally -- live-verified 2026-09-03, a hung NVIDIA
            # call with no deadline anywhere took 15m22s to finally give up.
            result = await asyncio.wait_for(
                Runner.run(agent, user_message),
                timeout=settings.opspilot_llm_provider_timeout_seconds,
            )
            trace = _extract_trace(result.new_items, result.final_output)
            trace_summary = _summarize_trace(trace)
            await asyncio.to_thread(
                _save_investigation, user_message, trace_summary, result.final_output
            )
            return result.final_output, provider, trace
        except TimeoutError as exc:
            logger.warning(
                "Provider '%s' timed out after %ss, falling back",
                provider,
                settings.opspilot_llm_provider_timeout_seconds,
            )
            last_error = exc
            continue
        except Exception as exc:  # noqa: BLE001 - fall through to next provider
            logger.warning("Provider '%s' failed, falling back: %s", provider, exc)
            last_error = exc
            continue

    raise RuntimeError("All configured LLM providers failed or are unconfigured") from last_error