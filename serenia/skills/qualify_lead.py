"""qualify_lead skill — LLM-powered lead qualification for event bookings (behind a feature flag)."""

import anthropic

from serenia.observability.tracing import trace_skill
from serenia.skills.airtable_client import get_table


def qualify_lead(name: str, email: str, message: str, conversation_context: str = "") -> dict:
    """Qualify an event lead using LLM reasoning. Scores the lead and writes results to Airtable.

    This skill is heavier than the others — it uses more tokens, takes longer,
    and has more surface area for hallucination. That's why it's behind a
    feature flag with a guarded rollout.
    """
    with trace_skill("qualify_lead", flag_key="qualify-lead-skill") as span:
        client = anthropic.Anthropic()

        full_context = (
            f"Lead Name: {name}\n"
            f"Lead Email: {email}\n"
            f"Lead Message: {message}\n"
        )
        if conversation_context:
            full_context += f"\nConversation Context:\n{conversation_context}"

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=(
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
            ),
            messages=[{"role": "user", "content": full_context}],
        )

        import json
        raw = response.content[0].text.strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {"score": "warm", "reason": "Could not parse LLM output", "action": "send_nurture"}

        # Write qualified lead to Airtable
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
            except Exception as e:
                print(f"[qualify_lead] Airtable write failed: {e}")
                span.set_tag("skill.airtable_error", str(e)[:200])
        else:
            print("[qualify_lead] Airtable not configured — skipping write")

        span.set_tag("skill.lead_score", result.get("score", "unknown"))
        span.set_tag("skill.lead_action", result.get("action", "unknown"))
        span.set_tag("skill.model", "claude-sonnet-4-20250514")
        span.set_tag("skill.input_length", len(full_context))

        return result
