"""qualify_lead skill — LLM-powered lead qualification for event bookings.

The skill still runs only when feature flag ``qualify-lead-skill`` is on
(checked in ``agent.py``). The model and system prompt come from LaunchDarkly
AI Config ``qualify-lead-config`` in project ``serenia-agent-skills``, evaluated
with ``launchdarkly-server-sdk-ai`` (``LDAIClient.completion_config``).

A successful Anthropic completion is wrapped in ``tracker.track_metrics_of``.
That records ``$ld:ai:generation:success``, which is the event key for the
existing metric ``ld_autogen__ai-completion-success`` (Completion success,
count, HigherThanBaseline). A provider exception records
``$ld:ai:generation:error`` instead and is re-raised.

After each qualification the skill also emits experiment custom events when
applicable: ``qualify-lead-accuracy`` (numeric 0/1 vs the two-signal rubric),
``qualify-lead-invalid-output`` (parse fail or missing score/action),
``qualify-lead-book-call`` (action is book_call), and
``qualify-lead-airtable-success`` (Leads row created).

Events are not emitted by importing this module. They are emitted when a
qualify-lead message is actually handled with ``LD_SDK_KEY`` set to this
project's server-side SDK key. The server SDK flushes custom events on its
interval (about 5 seconds) and on ``LDClient.close()`` (CLI shutdown and the
FastAPI shutdown hook).
"""

import json
import re

import anthropic
from ldai import AICompletionConfigDefault, LDMessage, ModelConfig, ProviderConfig
from ldai.providers.types import LDAIMetrics
from ldai.tracker import TokenUsage

from serenia.flags import get_ai_client, track_custom_event, user_context
from serenia.observability.tracing import trace_skill
from serenia.skills.airtable_client import get_table


AI_CONFIG_KEY = "qualify-lead-config"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 500

# Used only when LaunchDarkly cannot evaluate the config (SDK offline or the
# flag payload is missing). A served variation replaces this prompt entirely.
_FALLBACK_SYSTEM_PROMPT = (
    "You are a lead qualification assistant for an event venue space. "
    "Analyze the lead information and respond with EXACTLY this JSON format:\n"
    '{"score": "hot|warm|cold", "reason": "brief explanation", '
    '"action": "book_call|send_nurture|deprioritize"}\n\n'
    "Scoring guide:\n"
    "- HOT: Ready to book — mentions specific date, guest count, event type, budget, "
    "or wants to schedule a tour/visit. Multiple concrete details = hot.\n"
    "- WARM: Interested but exploring — has an event type in mind but missing key details "
    "(no date, no guest count), or is comparing venues.\n"
    "- COLD: Vague inquiry, just browsing, or event is very far out with no commitment signals.\n\n"
    "Respond with ONLY the JSON object, no other text."
)

_UNPARSED_RESULT = {
    "score": "warm",
    "reason": "Could not parse LLM output",
    "action": "send_nurture",
}

_EVENT_TYPE_RE = re.compile(
    r"\b(wedding|reception|shower|birthday|corporate|party|dinner|gala|"
    r"anniversary|fundraiser|retreat|conference|meeting)\b",
    re.I,
)
_GUEST_COUNT_RE = re.compile(
    r"\b(\d{1,4})\s*(guests?|people|attendees|pax)\b|\bfor\s+(\d{1,4})\b",
    re.I,
)
_DATE_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|"
    r"october|november|december|\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?|"
    r"\d{1,2}(st|nd|rd|th))\b",
    re.I,
)
_BUDGET_RE = re.compile(
    r"\$\s*\d|\bbudget\b|\bpackage\b|\bspend\b|\bcatering package\b",
    re.I,
)
_TOUR_RE = re.compile(
    r"\b(tour|walkthrough|walk-through|visit|see the (space|venue)|on[- ]site)\b",
    re.I,
)


def detect_booking_signals(text: str) -> list[str]:
    """Return concrete booking-signal labels present in lead text."""
    haystack = text or ""
    found: list[str] = []
    if _DATE_RE.search(haystack):
        found.append("specific_date")
    if _GUEST_COUNT_RE.search(haystack):
        found.append("guest_count")
    if _EVENT_TYPE_RE.search(haystack):
        found.append("event_type")
    if _BUDGET_RE.search(haystack):
        found.append("budget")
    if _TOUR_RE.search(haystack):
        found.append("tour_request")
    return found


def expected_qualification(signals: list[str]) -> tuple[str, str]:
    """Map signal count onto the two-signal hot rubric."""
    count = len(signals)
    if count >= 2:
        return "hot", "book_call"
    if count == 1:
        return "warm", "send_nurture"
    return "cold", "deprioritize"


def _emit_qualification_metrics(
    *,
    context_key: str,
    lead_text: str,
    result: dict,
    parsed_ok: bool,
    airtable_ok: bool | None,
) -> None:
    """Emit custom events for the scoring-rubric experiment metrics."""
    score = str(result.get("score") or "").strip().lower()
    action = str(result.get("action") or "").strip()
    missing_fields = not score or not action

    if (not parsed_ok) or missing_fields:
        track_custom_event("qualify-lead-invalid-output", context_key)
        return

    signals = detect_booking_signals(lead_text)
    expected_score, expected_action = expected_qualification(signals)
    accurate = 1.0 if score == expected_score and action == expected_action else 0.0
    track_custom_event(
        "qualify-lead-accuracy",
        context_key,
        data={
            "score": score,
            "action": action,
            "expected_score": expected_score,
            "expected_action": expected_action,
            "signals": signals,
        },
        metric_value=accurate,
    )
    if action == "book_call":
        track_custom_event("qualify-lead-book-call", context_key)
    if airtable_ok:
        track_custom_event("qualify-lead-airtable-success", context_key)



def _fallback_config() -> AICompletionConfigDefault:
    return AICompletionConfigDefault(
        enabled=True,
        model=ModelConfig(name=DEFAULT_MODEL, parameters={"max_tokens": DEFAULT_MAX_TOKENS}),
        provider=ProviderConfig(name="Anthropic"),
        messages=[LDMessage(role="system", content=_FALLBACK_SYSTEM_PROMPT)],
    )


def _max_tokens(model) -> int:
    if model is None:
        return DEFAULT_MAX_TOKENS
    for key in ("max_tokens", "maxTokens"):
        value = model.get_parameter(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return DEFAULT_MAX_TOKENS


def _anthropic_request(ai_config, user_content: str) -> tuple[str | None, list[dict], str, int]:
    """Split config messages into Anthropic's system prompt plus chat messages."""
    system_parts: list[str] = []
    messages: list[dict] = []
    for message in ai_config.messages or []:
        content = message.content if isinstance(message.content, str) else str(message.content)
        if message.role == "system":
            system_parts.append(content)
        else:
            messages.append({"role": message.role, "content": content})
    messages.append({"role": "user", "content": user_content})
    system = "\n\n".join(part for part in system_parts if part) or None
    model_name = ai_config.model.name if ai_config.model else DEFAULT_MODEL
    return system, messages, model_name, _max_tokens(ai_config.model)


def _anthropic_metrics(response) -> LDAIMetrics:
    """Map an Anthropic Messages response onto the AI SDK success metrics.

    ``track_metrics_of`` only calls this after the provider returns. Returning
    ``success=True`` is what emits ``$ld:ai:generation:success``.
    """
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return LDAIMetrics(
        success=True,
        tokens=TokenUsage(
            total=input_tokens + output_tokens,
            input=input_tokens,
            output=output_tokens,
        ),
    )


def _response_text(response) -> str:
    block = response.content[0]
    text = getattr(block, "text", None)
    if text is None and isinstance(block, dict):
        text = block.get("text", "")
    return (text or "").strip()


def _parse_model_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("expected object", text, 0)
    return parsed


def normalize_qualification(payload: dict) -> dict:
    """Map AI Config JSON onto the ``score`` / ``action`` / ``reason`` shape.

    ``qualify-lead-v1-stable`` returns ``lead_score`` and ``follow_up_action``.
    ``qualify-lead-v2-precise`` returns ``lead_temperature`` and
    ``follow_up_action`` (plus urgency, budget, and authority fields) and has
    no reason string. ``agent.py`` and the dashboard read ``score`` and
    ``action``; scores are lowercased so the hot/warm/cold badge styles match.
    """
    score = "warm"
    for key in ("score", "lead_score", "lead_temperature"):
        value = payload.get(key)
        if value:
            score = str(value).strip().lower()
            break

    action = "send_nurture"
    for key in ("action", "follow_up_action"):
        value = payload.get(key)
        if value:
            action = str(value).strip()
            break

    reason = str(payload.get("reason") or "").strip()
    if not reason:
        details = []
        for key in ("urgency", "budget_signal", "decision_authority"):
            value = payload.get(key)
            if value:
                details.append(f"{key.replace('_', ' ')}: {value}")
        reason = "; ".join(details)

    return {"score": score, "action": action, "reason": reason}


def _lead_context(name: str, email: str, message: str, conversation_context: str) -> str:
    full_context = (
        f"Lead Name: {name}\n"
        f"Lead Email: {email}\n"
        f"Lead Message: {message}\n"
    )
    if conversation_context:
        full_context += f"\nConversation Context:\n{conversation_context}"
    return full_context


def _write_airtable(name: str, email: str, message: str, result: dict, span) -> bool | None:
    """Write the lead to Airtable. True on success, False on failure, None if skipped."""
    table = get_table("Leads")
    if table:
        try:
            action_map = {
                "book_call": "Scheduled call",
                "send_nurture": "Sent brochure",
                "deprioritize": "Sent product info",
            }
            record = {
                "Name": name,
                "Email": email,
                "Message": message,
                "Status": "Qualified",
                "Lead Score": result.get("score", "warm").capitalize(),
                "Lead Action": action_map.get(result.get("action", ""), result.get("action", "")),
                "Qualification Reason": result.get("reason", ""),
            }
            airtable_result = table.create(record)
            record_id = airtable_result["id"]
            span.set_tag("skill.airtable_record_id", record_id)
            print(f"[qualify_lead] Created Airtable record: {record_id}")
            return True
        except Exception as e:
            print(f"[qualify_lead] Airtable write failed: {e}")
            span.set_tag("skill.airtable_error", str(e)[:200])
            return False
    print("[qualify_lead] Airtable not configured — skipping write")
    return None


def qualify_lead(
    name: str,
    email: str,
    message: str,
    conversation_context: str = "",
    context_key: str = "anonymous",
) -> dict:
    """Qualify an event lead using the LaunchDarkly AI Config and write results to Airtable.

    This skill is heavier than the others — it uses more tokens, takes longer,
    and has more surface area for hallucination. That's why it's behind a
    feature flag with a guarded rollout.
    """
    with trace_skill("qualify_lead", flag_key="qualify-lead-skill") as span:
        full_context = _lead_context(name, email, message, conversation_context)
        context = user_context(context_key)
        ai_config = get_ai_client().completion_config(
            AI_CONFIG_KEY,
            context,
            _fallback_config(),
            variables={
                "name": name,
                "email": email,
                "message": message,
                "conversation_context": conversation_context,
            },
        )

        provider_name = ai_config.provider.name if ai_config.provider else ""
        model_name = ai_config.model.name if ai_config.model else ""
        span.set_tag("skill.ai_config", AI_CONFIG_KEY)
        span.set_tag("skill.ai_config_enabled", ai_config.enabled)
        span.set_tag("skill.model", model_name or "none")
        span.set_tag("skill.input_length", len(full_context))
        print(
            f"[qualify_lead] {AI_CONFIG_KEY} for '{context_key}' "
            f"enabled={ai_config.enabled} provider={provider_name or 'none'} model={model_name or 'none'}"
        )

        parsed_ok = True
        if not ai_config.enabled or not model_name:
            print("[qualify_lead] AI config disabled or missing a model — skipping generation")
            result = {
                "score": "warm",
                "reason": "AI config disabled",
                "action": "send_nurture",
            }
        elif provider_name.lower() not in ("", "anthropic"):
            print(f"[qualify_lead] Unsupported provider '{provider_name}' — skipping generation")
            result = {
                "score": "warm",
                "reason": f"Unsupported AI provider: {provider_name}",
                "action": "send_nurture",
            }
        else:
            system, messages, model_name, max_tokens = _anthropic_request(ai_config, full_context)
            span.set_tag("skill.model", model_name)
            client = anthropic.Anthropic()
            tracker = ai_config.create_tracker()

            def call_anthropic():
                kwargs = {
                    "model": model_name,
                    "max_tokens": max_tokens,
                    "messages": messages,
                }
                if system:
                    kwargs["system"] = system
                return client.messages.create(**kwargs)

            # track_metrics_of records duration, tokens, and either
            # $ld:ai:generation:success or $ld:ai:generation:error.
            response = tracker.track_metrics_of(_anthropic_metrics, call_anthropic)
            try:
                result = normalize_qualification(_parse_model_json(_response_text(response)))
                parsed_ok = True
            except (json.JSONDecodeError, IndexError, AttributeError):
                result = dict(_UNPARSED_RESULT)
                parsed_ok = False

        airtable_ok = _write_airtable(name, email, message, result, span)
        _emit_qualification_metrics(
            context_key=context_key,
            lead_text=full_context,
            result=result,
            parsed_ok=parsed_ok,
            airtable_ok=airtable_ok,
        )
        span.set_tag("skill.lead_score", result.get("score", "unknown"))
        span.set_tag("skill.lead_action", result.get("action", "unknown"))
        return result
