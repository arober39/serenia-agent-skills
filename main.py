"""Serenia Agent — Entry point.

Simulates incoming customer messages to an event venue,
demonstrating skill routing, feature flags, and observability.
"""

from dotenv import load_dotenv

load_dotenv()

from serenia.observability.tracing import init_tracing
from serenia.flags import init_launchdarkly, shutdown as shutdown_ld


# Initialize tracing and LaunchDarkly before processing messages
tracer = init_tracing()
ld_client = init_launchdarkly()

from serenia.agent import handle_message


# --- Simulated customer messages ---

DEMO_MESSAGES = [
    {
        "id": "msg-1",
        "context_key": "customer-olivia",
        "message": "Hi! What types of events do you host? I'm thinking about a baby shower.",
        "description": "FAQ question -> routes to answer_faq",
    },
    {
        "id": "msg-2",
        "context_key": "customer-marcus",
        "message": (
            "Hey, I'm Marcus Chen, marcus@greenleafplants.co. We're looking for a space "
            "for our team events. Can someone reach out to tell us more?"
        ),
        "description": "New inquiry with contact info -> routes to log_inquiry -> Airtable",
    },
    {
        "id": "msg-3",
        "context_key": "customer-dana",
        "message": (
            "Hi, I'm Dana Rivera (dana@riveraphotography.com). I'm planning my wedding reception "
            "for September 20th — expecting about 120 guests. We'd need the full catering package "
            "and decor setup. Budget is around $8k total. Can we schedule a tour this week?"
        ),
        "description": "Hot lead with date + guest count + budget -> routes to qualify_lead (if flag enabled) -> Airtable",
    },
    {
        "id": "msg-4",
        "context_key": "customer-james",
        "message": "Do you allow outside caterers? And is there parking for about 50 cars?",
        "description": "FAQ about catering + parking -> routes to answer_faq",
    },
    {
        "id": "msg-5",
        "context_key": "customer-priya",
        "message": "How much does venue rental cost for a weekend evening event?",
        "description": "Pricing FAQ -> routes to answer_faq",
    },
    {
        "id": "msg-6",
        "context_key": "customer-alex",
        "message": "Can you put together a custom quote for a 3-day corporate retreat with full catering and AV setup?",
        "description": "Proposal request -> routes to auto_propose (locked) -> coming soon message",
    },
]


def main():
    print("=" * 60)
    print("  SERENIA — Event Venue AI Agent")
    print("  Demo: Skill Routing + Feature Flags + Observability")
    print("=" * 60)

    for msg in DEMO_MESSAGES:
        print(f"\n{'─' * 60}")
        print(f"  Message {msg['id']}: {msg['description']}")
        print(f"  From: {msg['context_key']}")
        print(f"  \"{msg['message'][:80]}{'...' if len(msg['message']) > 80 else ''}\"")
        print(f"{'─' * 60}")

        response = handle_message(msg["message"], context_key=msg["context_key"])

        print(f"\n  Serenia: {response}")

    print(f"\n{'=' * 60}")
    print("  Demo complete. Check Datadog / LaunchDarkly for traces.")
    print("=" * 60)

    # Clean shutdown
    shutdown_ld()


if __name__ == "__main__":
    main()
