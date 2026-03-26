"""log_inquiry skill — logs customer inquiries to Airtable CRM."""

from serenia.observability.tracing import trace_skill
from serenia.skills.airtable_client import get_table


def log_inquiry(name: str, email: str, message: str) -> str:
    """Log a customer inquiry to the Airtable Leads table."""
    with trace_skill("log_inquiry") as span:
        record = {
            "Name": name,
            "Email": email,
            "Message": message,
            "Status": "New",
        }

        table = get_table("Leads")
        if table:
            try:
                result = table.create(record)
                record_id = result["id"]
                span.set_tag("skill.airtable_record_id", record_id)
                print(f"[log_inquiry] Created Airtable record: {record_id}")
            except Exception as e:
                print(f"[log_inquiry] Airtable write failed: {e}")
                span.set_tag("skill.airtable_error", str(e)[:200])
        else:
            print("[log_inquiry] Airtable not configured — skipping write")

        span.set_tag("skill.customer_name", name)

        return f"Got it! I've logged your inquiry, {name}. We'll follow up at {email} within 24 hours."
