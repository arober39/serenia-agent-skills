"""LaunchDarkly SDK hook that attaches flag evaluation context to Datadog traces."""

from ddtrace import tracer
from ldclient.hook import Hook, EvaluationSeriesContext


class DatadogTracingHook(Hook):
    """Attaches LaunchDarkly flag evaluation metadata to active Datadog spans.

    This is what allows LaunchDarkly to correlate Datadog trace data with
    specific flag evaluations — powering Guarded Rollouts.
    """

    @property
    def metadata(self):
        from ldclient.hook import Metadata
        return Metadata(name="DatadogTracingHook")

    def before_evaluation(self, series_context: EvaluationSeriesContext, data: dict) -> dict:
        return data

    def after_evaluation(self, series_context: EvaluationSeriesContext, data: dict, detail) -> dict:
        span = tracer.current_span()
        if span is None:
            return data

        flag_key = series_context.key
        context = series_context.context

        span.set_tag("feature_flag.key", flag_key)
        span.set_tag("feature_flag.provider_name", "LaunchDarkly")
        span.set_tag("feature_flag.result.value", str(detail.value))

        if hasattr(detail, "variation_index") and detail.variation_index is not None:
            span.set_tag("feature_flag.result.variant", str(detail.variation_index))

        if context:
            span.set_tag("feature_flag.context.key", context.key)

        return data
