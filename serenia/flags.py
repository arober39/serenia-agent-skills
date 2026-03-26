"""LaunchDarkly SDK initialization and flag evaluation for Serenia skills."""

import os

import ldclient
from ldclient import Context
from ldclient.config import Config

from serenia.observability.ld_hook import DatadogTracingHook


_client: ldclient.LDClient | None = None


def init_launchdarkly():
    """Initialize the LaunchDarkly SDK client with the Datadog tracing hook."""
    global _client

    sdk_key = os.environ.get("LD_SDK_KEY", "")
    if not sdk_key:
        print("[flags] WARNING: LD_SDK_KEY not set — all flags will return defaults")
        config = Config(sdk_key="placeholder", offline=True)
    else:
        config = Config(
            sdk_key=sdk_key,
            hooks=[DatadogTracingHook()],
        )

    ldclient.set_config(config)
    _client = ldclient.get()

    if _client.is_initialized():
        print("[flags] LaunchDarkly SDK initialized successfully")
    else:
        print("[flags] LaunchDarkly SDK failed to initialize — using defaults")

    return _client


def get_client() -> ldclient.LDClient:
    """Get the LaunchDarkly client, initializing if needed."""
    global _client
    if _client is None:
        init_launchdarkly()
    return _client


def is_skill_enabled(skill_name: str, context_key: str = "anonymous") -> bool:
    """Check if a skill is enabled via its LaunchDarkly feature flag.

    Flag key convention: {skill_name} with underscores replaced by hyphens.
    e.g., qualify_lead -> qualify-lead-skill
    """
    flag_key = f"{skill_name.replace('_', '-')}-skill"
    client = get_client()

    context = Context.builder(context_key).kind("user").name(context_key).build()

    result = client.variation(flag_key, context, default=False)
    print(f"[flags] {flag_key} for '{context_key}' -> {result}")
    return result


def shutdown():
    """Shut down the LaunchDarkly client."""
    global _client
    if _client:
        _client.close()
        _client = None
