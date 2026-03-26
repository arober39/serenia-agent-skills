"""Datadog APM tracing setup for Serenia agent skills.

APM traces flow through the Datadog Agent, which forwards them to
LaunchDarkly via apm_dd_url for Guarded Rollouts observability.
"""

import os

from ddtrace import tracer, patch


def init_tracing():
    """Initialize Datadog APM tracing with Anthropic auto-instrumentation.

    The tracer automatically reads DD_AGENT_HOST from the environment
    and sends APM traces to the Datadog Agent on port 8126.

    Required env vars:
    - DD_AGENT_HOST: Datadog Agent hostname (e.g. localhost)

    Optional env vars:
    - DD_SERVICE: Service name in traces (default: serenia-agent)
    - DD_ENV: Environment tag (default: demo)
    """
    os.environ.setdefault("DD_SERVICE", "serenia-agent")
    os.environ.setdefault("DD_ENV", "demo")
    os.environ.setdefault("DD_VERSION", "0.1.0")

    # Auto-instrument Anthropic SDK calls
    patch(anthropic=True)

    agent_host = os.environ.get("DD_AGENT_HOST", "")
    mode = f"agent ({agent_host})" if agent_host else "direct"
    print(f"[tracing] APM tracing enabled — service=serenia-agent, mode={mode}")

    return tracer


def trace_skill(skill_name: str, flag_key: str | None = None):
    """Create a traced span for a skill invocation.

    Uses LLMObs.workflow() when LLM Observability is active,
    falls back to basic tracer span otherwise.

    Usage:
        with trace_skill("answer_faq") as span:
            result = do_work()
            span.set_tag("result.length", len(result))
    """
    span = tracer.trace(
        f"serenia.skill.{skill_name}",
        service="serenia-agent",
        resource=skill_name,
    )
    span.set_tag("skill.name", skill_name)
    if flag_key:
        span.set_tag("feature_flag.key", flag_key)
    return span
