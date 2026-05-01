"""qualify_lead skill — LLM-powered lead qualification driven by an AI Config."""

import json

import anthropic

from serenia.flags import build_context, get_ai_client
from serenia.observability.tracing import trace_skill
from serenia.skills.airtable_client import get_table


AI_CONFIG_KEY = "qualify-lead-config"


def qualify_lead(name: str, email: str, message: str, conversation_context: str = "") -> dict:
    """Qualify an event lead using whichever prompt/model the AI Config serves.

    The AI Config (`qualify-lead-config`) supplies the system prompt and model.
    Each variation is expected to instruct Claude to return a JSON object with
    a `lead_score` field. The parser reads `response["lead_score"]` directly —
    if a variation returns a different shape, this raises KeyError.
    """
    with trace_skill("qualify_lead", flag_key=AI_CONFIG_KEY) as span:
        full_context = (
            f"Lead Name: {name}\n"
            f"Lead Email: {email}\n"
            f"Lead Message: {message}\n"
        )
        if conversation_context:
            full_context += f"\nConversation Context:\n{conversation_context}"

        ai_client = get_ai_client()
        ld_context = build_context(email if email and email != "not provided" else name)
        ai_config = ai_client.completion_config(AI_CONFIG_KEY, ld_context)

        model_name = ai_config.model.name if ai_config.model else "claude-sonnet-4-20250514"
        variation_messages = ai_config.messages or []

        system_parts = [m.content for m in variation_messages if m.role == "system"]
        non_system = [
            {"role": m.role, "content": m.content}
            for m in variation_messages
            if m.role != "system"
        ]
        anthropic_messages = non_system + [{"role": "user", "content": full_context}]

        span.set_tag("ai_config.key", AI_CONFIG_KEY)
        span.set_tag("ai_config.model", model_name)
        span.set_tag("ai_config.enabled", ai_config.enabled)

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model_name,
            max_tokens=500,
            system="\n\n".join(system_parts) if system_parts else anthropic.NOT_GIVEN,
            messages=anthropic_messages,
        )

        raw = response.content[0].text.strip()
        parsed = json.loads(raw)

        # Intentionally non-defensive: a variation that returns a different shape
        # (e.g. `lead_temperature` instead of `lead_score`) will raise KeyError here.
        lead_score = parsed["lead_score"]
        follow_up_action = parsed["follow_up_action"]

        result = {
            "lead_score": lead_score,
            "follow_up_action": follow_up_action,
            "raw": parsed,
        }

        table = get_table("Leads")
        if table:
            try:
                action_label_map = {
                    "book_call": "Scheduled call",
                    "send_nurture": "Sent brochure",
                    "deprioritize": "Sent product info",
                }
                record = {
                    "Name": name,
                    "Email": email,
                    "Message": message,
                    "Status": "Qualified",
                    "Lead Score": lead_score,
                    "Lead Action": action_label_map.get(follow_up_action, follow_up_action),
                }
                airtable_result = table.create(record)
                record_id = airtable_result["id"]
                span.set_tag("skill.airtable_record_id", record_id)
                print(f"[qualify_lead] Created Airtable record: {record_id}")
            except Exception as e:
                print(f"[qualify_lead] Airtable write failed: {e}")
                span.set_tag("skill.airtable_error", str(e)[:200])
        else:
            print("[qualify_lead] Airtable not configured — skipping write")

        span.set_tag("skill.lead_score", lead_score)
        span.set_tag("skill.follow_up_action", follow_up_action)
        span.set_tag("skill.model", model_name)
        span.set_tag("skill.input_length", len(full_context))

        return result
