"""Agent definition and the single entry point (`run_chat_turn`) the API
layer calls. This is the only file that constructs an Agent or calls Runner.
"""
from __future__ import annotations

import logging

from agents import Agent, Runner

from app.agent.providers import ProviderNotConfiguredError, build_model
from app.core.config import LLMProviderName, get_settings
from app.tools.cloudwatch_tools import get_ec2_cpu_utilization
from app.tools.ec2_tools import list_ec2_instances

logger = logging.getLogger(__name__)

AGENT_INSTRUCTIONS = (
    "You are OpsPilot, a read-only DevOps investigation assistant for a single "
    "AWS account. Use the available tools to answer questions about EC2 "
    "instances and their CloudWatch CPU utilization — never guess at live "
    "infrastructure state. When asked whether something is 'over 80% CPU', "
    "call get_ec2_cpu_utilization and check its breached_80_percent field "
    "rather than estimating. You cannot take any write/mutating action; if "
    "asked to change something, say so plainly."
)

TOOLS = [list_ec2_instances, get_ec2_cpu_utilization]


def _build_agent(provider: LLMProviderName) -> Agent:
    model = build_model(provider)
    return Agent(name="OpsPilot", instructions=AGENT_INSTRUCTIONS, tools=TOOLS, model=model)


async def run_chat_turn(user_message: str) -> tuple[str, str]:
    """Run one chat turn, falling back across providers on failure.

    Returns (reply_text, provider_that_answered).
    Fallback order is settings.provider_order: configured primary first,
    then the rest of the fixed groq -> gemini -> nvidia chain.
    """
    settings = get_settings()
    last_error: Exception | None = None

    for provider in settings.provider_order:
        try:
            agent = _build_agent(provider)
        except ProviderNotConfiguredError as exc:
            logger.info("Skipping unconfigured provider '%s'", provider)
            last_error = exc
            continue

        try:
            result = await Runner.run(agent, user_message)
            return result.final_output, provider
        except Exception as exc:  # noqa: BLE001 - any provider failure should fall through to the next one
            logger.warning("Provider '%s' failed, falling back: %s", provider, exc)
            last_error = exc
            continue

    raise RuntimeError("All configured LLM providers failed or are unconfigured") from last_error
