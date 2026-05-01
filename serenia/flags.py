"""LaunchDarkly SDK initialization for Serenia.

Wires three things into one client:
  1. The base ldclient (flag evaluation).
  2. The LaunchDarkly Observability plugin (errors, traces, sessions to LD).
  3. The LaunchDarkly AI Config client (served prompts/models for skills).

The legacy Datadog tracing hook is preserved so existing ddtrace-based traces
continue to carry flag evaluation tags, but it is no longer required for the
demo — observability flows through the LD plugin.
"""

import os

import ldclient
from ldclient import Context
from ldclient.config import Config
from ldai.client import LDAIClient
from ldobserve import ObservabilityConfig, ObservabilityPlugin

from serenia.observability.ld_hook import DatadogTracingHook


_client: ldclient.LDClient | None = None
_ai_client: LDAIClient | None = None


def init_launchdarkly():
    """Initialize the LaunchDarkly SDK with the observability plugin and AI Config client."""
    global _client, _ai_client

    sdk_key = os.environ.get("LD_SDK_KEY", "")
    if not sdk_key:
        print("[flags] WARNING: LD_SDK_KEY not set — all flags will return defaults")
        config = Config(sdk_key="placeholder", offline=True)
    else:
        observability_plugin = ObservabilityPlugin(
            ObservabilityConfig(
                service_name="serenia-agent",
                service_version="0.1.0",
            )
        )
        config = Config(
            sdk_key=sdk_key,
            hooks=[DatadogTracingHook()],
            plugins=[observability_plugin],
        )

    ldclient.set_config(config)
    _client = ldclient.get()

    if _client.is_initialized():
        print("[flags] LaunchDarkly SDK initialized successfully")
    else:
        print("[flags] LaunchDarkly SDK failed to initialize — using defaults")

    _ai_client = LDAIClient(_client)
    print("[flags] LaunchDarkly AI Config client ready")

    return _client


def get_client() -> ldclient.LDClient:
    """Get the LaunchDarkly client, initializing if needed."""
    global _client
    if _client is None:
        init_launchdarkly()
    return _client


def get_ai_client() -> LDAIClient:
    """Get the LaunchDarkly AI Config client, initializing if needed."""
    global _ai_client
    if _ai_client is None:
        init_launchdarkly()
    return _ai_client


def build_context(context_key: str = "anonymous") -> Context:
    return Context.builder(context_key).kind("user").name(context_key).build()


def shutdown():
    """Shut down the LaunchDarkly client."""
    global _client, _ai_client
    if _client:
        _client.close()
        _client = None
        _ai_client = None
