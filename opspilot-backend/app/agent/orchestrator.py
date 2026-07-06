"""Agent definition and the single entry point (`run_chat_turn`) the API
layer calls. This is the only file that constructs an Agent or calls Runner.
"""
from __future__ import annotations

import json
import logging

from agents import Agent, ItemHelpers, Runner
from agents.items import MessageOutputItem, ToolCallItem, ToolCallOutputItem

from app.agent.providers import ProviderNotConfiguredError, build_model
from app.core.config import LLMProviderName, get_settings
from app.models.chat import TraceStep
from app.tools.cloudtrail_tools import get_recent_account_activity, list_recent_ec2_activity
from app.tools.cloudwatch_tools import get_ec2_cpu_utilization
from app.tools.dynamodb_tools import list_dynamodb_tables
from app.tools.ec2_tools import get_ec2_status_check, list_ec2_instances
from app.tools.lambda_tools import list_lambda_functions
from app.tools.rds_tools import get_rds_status
from app.tools.s3_tools import list_s3_buckets
from app.tools.sns_tools import list_sns_topics

logger = logging.getLogger(__name__)

AGENT_INSTRUCTIONS = (
    "You are OpsPilot, a read-only DevOps investigation assistant for a single "
    "AWS account. Never guess at live infrastructure state — always use tools.\n\n"
    "For a simple lookup ('what instances are running', 'list my S3 buckets', "
    "'what Lambda functions do I have'), just call the relevant tool and answer "
    "directly — no investigation protocol needed for these.\n\n"
    "For an investigation question ('why is X slow', 'is anything wrong with "
    "this instance', 'diagnose...', 'what happened last night'), follow this "
    "protocol instead of a single lookup:\n"
    "1. State the hypothesis you're testing in one short sentence before each "
    "tool call, e.g. 'Checking whether CPU load explains this.'\n"
    "2. Check CPU utilization first (get_ec2_cpu_utilization). If it breached "
    "80%, that's your leading suspect — explain why and you can stop there.\n"
    "3. If CPU is normal, don't stop — check get_ec2_status_check next, to "
    "rule out an infrastructure-level fault (bad host, failed checks) as "
    "opposed to a load-level one.\n"
    "4. If status checks pass too, check list_recent_ec2_activity — a "
    "perceived issue is often explained by something someone actually did "
    "(stopped/started/rebooted/modified the instance), not a real fault.\n"
    "5. Conclude with a short summary: which hypotheses you tested, which "
    "were ruled out and why, and your final conclusion. If nothing found "
    "explains the issue, say so plainly rather than inventing a cause.\n\n"
    "You cannot take any write/mutating action; if asked to change something, "
    "say so plainly."
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
    for item in new_items:
        if isinstance(item, ToolCallItem):
            raw = item.raw_item
            steps.append(
                TraceStep(
                    type="tool_call",
                    tool=getattr(raw, "name", "unknown_tool"),
                    arguments=_try_parse_json(getattr(raw, "arguments", "{}")),
                )
            )
        elif isinstance(item, ToolCallOutputItem):
            steps.append(TraceStep(type="tool_result", output=_try_parse_json(item.output)))
        elif isinstance(item, MessageOutputItem):
            text = ItemHelpers.text_message_output(item)
            if text and text.strip():
                steps.append(TraceStep(type="message", text=text.strip()))

    # Drop a trailing message step that duplicates the final reply.
    if steps and steps[-1].type == "message" and steps[-1].text == final_output.strip():
        steps.pop()

    return steps


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
            result = await Runner.run(agent, user_message)
            trace = _extract_trace(result.new_items, result.final_output)
            return result.final_output, provider, trace
        except Exception as exc:  # noqa: BLE001 - fall through to next provider
            logger.warning("Provider '%s' failed, falling back: %s", provider, exc)
            last_error = exc
            continue

    raise RuntimeError("All configured LLM providers failed or are unconfigured") from last_error
