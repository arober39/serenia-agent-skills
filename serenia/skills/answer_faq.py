"""answer_faq skill — answers customer questions about the event venue."""

import json
import os

import anthropic

from serenia.observability.tracing import trace_skill

# Load knowledge base at module level
_kb_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "knowledge_base.json")
with open(_kb_path) as f:
    KNOWLEDGE_BASE = json.load(f)


def answer_faq(question: str) -> str:
    """Answer a customer question using the knowledge base and LLM."""
    with trace_skill("answer_faq") as span:
        kb_text = "\n".join(
            f"Q: {entry['question']}\nA: {entry['answer']}"
            for entry in KNOWLEDGE_BASE
        )

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system=(
                "You are Serenia, the AI assistant for an event venue space. "
                "You're warm, helpful, and knowledgeable about the venue and its services. "
                "The venue hosts everything from baby showers to business networking events to wedding receptions. "
                "Services include venue rental, decor/setup packages, in-house catering, and catering partner referrals.\n\n"
                "Answer the customer's question using ONLY the knowledge base below. "
                "Use markdown formatting — bullet points, bold text, and headers where appropriate to make "
                "the response easy to scan. Keep answers concise but complete.\n\n"
                "If the answer isn't in the knowledge base, say you'll connect them with "
                "the events team who can help.\n\n"
                f"Knowledge Base:\n{kb_text}"
            ),
            messages=[{"role": "user", "content": question}],
        )

        answer = response.content[0].text
        span.set_tag("skill.input_length", len(question))
        span.set_tag("skill.output_length", len(answer))
        span.set_tag("skill.model", "claude-sonnet-4-20250514")

        return answer
