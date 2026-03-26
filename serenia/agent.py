"""Serenia agent core — intent detection and skill routing for an event venue business."""

import json
import time

import anthropic

from serenia.observability.tracing import trace_skill
from serenia.flags import is_skill_enabled
from serenia.skills.answer_faq import answer_faq
from serenia.skills.log_inquiry import log_inquiry
from serenia.skills.qualify_lead import qualify_lead


SKILL_REGISTRY = {
    "answer_faq": {
        "description": "Answer questions about the venue, services, and pricing",
        "status": "stable",
        "function": answer_faq,
    },
    "log_inquiry": {
        "description": "Log a potential client inquiry to Airtable CRM",
        "status": "stable",
        "function": log_inquiry,
    },
    "qualify_lead": {
        "description": "Score and qualify an event lead, write results to Airtable",
        "status": "new",
        "flag_key": "qualify-lead-skill",
        "function": qualify_lead,
    },
    "auto_propose": {
        "description": "Generate a custom event proposal with pricing (coming soon)",
        "status": "locked",
        "function": None,
    },
}


def get_skill_registry_info() -> list[dict]:
    """Return skill registry as a serializable list for the UI."""
    return [
        {
            "name": name,
            "description": info["description"],
            "status": info["status"],
            "flag_key": info.get("flag_key"),
        }
        for name, info in SKILL_REGISTRY.items()
    ]


def detect_intent(message: str) -> dict:
    """Use LLM to detect customer intent and extract relevant info."""
    with trace_skill("intent_detection") as span:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=(
                "You are an intent classifier for Serenia, an AI assistant for an event venue space. "
                "The venue hosts events ranging from baby showers and birthday parties to business networking events, "
                "wedding receptions, corporate retreats, and private dinners. Services include venue rental, "
                "decor/setup packages (or DIY decor), in-house catering, and catering partner referrals.\n\n"
                "Analyze the customer message and respond with EXACTLY this JSON format:\n"
                '{"skill": "answer_faq|log_inquiry|qualify_lead|auto_propose", '
                '"name": "customer name or null", '
                '"email": "customer email or null", '
                '"question": "the core question or message"}\n\n'
                "ROUTING RULES (evaluate in this order — use the FIRST match):\n\n"
                "1. auto_propose: Customer explicitly asks for a proposal, quote, estimate, pricing package, "
                "or custom event plan to be sent to them. Keywords: 'proposal', 'quote', 'send me pricing', "
                "'put together a package', 'estimate for my event'. Even if they mention event details, "
                "if they are asking for a DOCUMENT or written pricing, route here.\n\n"
                "2. qualify_lead: Customer shows strong booking intent. Must include AT LEAST TWO of these: "
                "specific event type (wedding reception, corporate event, baby shower, etc.), "
                "guest count or expected attendance, specific date or timeframe ('June 15', 'next March', 'this fall'), "
                "budget or willingness to discuss pricing for THEIR event, request to book a tour or visit, "
                "mention of specific services needed (catering, decor, bar service). "
                "Having contact info alone is NOT enough — they must show intent to book.\n\n"
                "3. log_inquiry: Customer provides their name AND/OR email/contact info. "
                "If a message contains an email address or the customer shares their name and asks to be contacted, "
                "this is ALWAYS log_inquiry (unless it matches auto_propose or qualify_lead above). "
                "Do NOT route to answer_faq if the customer provides contact info. "
                "Examples: 'Can someone reach out?', 'I'd love to learn more, my email is...', "
                "'My name is X, here's my email', 'Please get in touch', 'I'm interested, contact me at...'.\n\n"
                "4. answer_faq: Customer is asking a general question about the venue — capacity, pricing ranges, "
                "what's included, catering options, decor policies, parking, accessibility, cancellation policy, "
                "availability, or anything informational. The customer has NOT provided any contact info (no email, no phone). "
                "Also use for greetings ('hi', 'hello'), off-topic messages, or anything that doesn't "
                "fit the other categories.\n\n"
                "EDGE CASES:\n"
                "- 'How much for a wedding reception?' (no date, no guest count) → answer_faq\n"
                "- 'How much for a wedding reception for 100 guests in October?' → qualify_lead\n"
                "- 'Send me a quote for a corporate event' → auto_propose\n"
                "- Name + email + 'I'm interested' (no event details) → log_inquiry\n"
                "- Name + email + 'wedding, 80 guests, June' → qualify_lead\n"
                "- 'Can I bring my own caterer?' → answer_faq\n"
                "- 'Do you do baby showers?' → answer_faq\n"
                "- 'I want to book a tour this week, I'm planning a holiday party for December' → qualify_lead\n\n"
                "Respond with ONLY the JSON object."
            ),
            messages=[{"role": "user", "content": message}],
        )

        raw = response.content[0].text.strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {"skill": "answer_faq", "name": None, "email": None, "question": message}

        span.set_tag("intent.skill", result.get("skill", "unknown"))
        span.set_tag("intent.has_name", result.get("name") is not None)
        span.set_tag("intent.has_email", result.get("email") is not None)

        return result


def process_message(message: str, context_key: str = "anonymous") -> dict:
    """Process an incoming customer message and return structured results."""
    start_time = time.time()
    metadata = {
        "context_key": context_key,
        "detected_intent": None,
        "routed_to": None,
        "flag_evaluated": None,
        "flag_result": None,
        "fallback": False,
        "lead_score": None,
        "lead_action": None,
        "latency_ms": None,
    }

    with trace_skill("agent.handle_message") as span:
        span.set_tag("input.message", message[:200])
        span.set_tag("input.context_key", context_key)

        # Step 1: Detect intent
        intent = detect_intent(message)
        skill_name = intent.get("skill", "answer_faq")
        metadata["detected_intent"] = skill_name
        metadata["extracted_name"] = intent.get("name")
        metadata["extracted_email"] = intent.get("email")

        # Step 2: Check skill registry
        skill_info = SKILL_REGISTRY.get(skill_name)
        if not skill_info:
            metadata["routed_to"] = "unknown"
            metadata["latency_ms"] = round((time.time() - start_time) * 1000)
            return {
                "response": "I'm sorry, I'm not sure how to help with that. Let me connect you with our events team.",
                "metadata": metadata,
            }

        if skill_info["status"] == "locked":
            metadata["routed_to"] = skill_name
            metadata["latency_ms"] = round((time.time() - start_time) * 1000)
            return {
                "response": (
                    "Custom event proposals are coming soon! For now, I can connect you with our events team "
                    "to put together a package. Just share your name, email, and event details and we'll reach out."
                ),
                "metadata": metadata,
            }

        # Step 3: Check feature flags for flagged skills
        if skill_info.get("flag_key"):
            flag_key = skill_info["flag_key"]
            metadata["flag_evaluated"] = flag_key
            flag_enabled = is_skill_enabled("qualify_lead", context_key)
            metadata["flag_result"] = flag_enabled

            if not flag_enabled:
                metadata["fallback"] = True
                skill_name = "log_inquiry"
                skill_info = SKILL_REGISTRY["log_inquiry"]
                span.set_tag("skill.fallback", True)

        metadata["routed_to"] = skill_name
        span.set_tag("skill.routed_to", skill_name)

        # Step 4: Route to skill
        if skill_name == "answer_faq":
            response = answer_faq(intent.get("question", message))

        elif skill_name == "log_inquiry":
            name = intent.get("name") or "Unknown"
            email = intent.get("email") or "not provided"
            response = log_inquiry(name, email, intent.get("question", message))

        elif skill_name == "qualify_lead":
            name = intent.get("name") or "Unknown"
            email = intent.get("email") or "not provided"
            result = qualify_lead(name, email, intent.get("question", message))
            score = result.get("score", "unknown")
            action = result.get("action", "unknown")
            reason = result.get("reason", "")
            metadata["lead_score"] = score
            metadata["lead_action"] = action

            if action == "book_call":
                response = (
                    f"Thanks {name}! Based on your event details, I'd love to get you on the calendar "
                    f"for a venue tour so you can see the space in person. "
                    f"I'll send a booking link to {email} shortly."
                )
            elif action == "send_nurture":
                response = (
                    f"Thanks for reaching out, {name}! I've noted your event details and will send "
                    f"some venue info, photos, and sample packages to {email}."
                )
            else:
                response = (
                    f"Thanks for your message, {name}! I've logged your inquiry and our events team "
                    f"will follow up if we're a good fit for your event."
                )
        else:
            response = "I'm not sure how to help with that. Let me connect you with our events team."

        metadata["latency_ms"] = round((time.time() - start_time) * 1000)

        return {"response": response, "metadata": metadata}


def handle_message(message: str, context_key: str = "anonymous") -> str:
    """Simple string-only wrapper for CLI usage."""
    result = process_message(message, context_key)
    return result["response"]
