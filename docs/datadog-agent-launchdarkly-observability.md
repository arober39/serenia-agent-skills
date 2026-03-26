# Using the Datadog Agent to Power LaunchDarkly Guarded Rollouts

If you're already using Datadog to monitor your application, you can forward your existing APM trace data to LaunchDarkly using the Datadog Agent — no additional instrumentation required. This unlocks **Guarded Rollouts**, letting LaunchDarkly automatically detect regressions tied to feature flag changes and roll them back before they impact users.

This guide walks through the full demo using **Serenia**, an AI-powered event venue assistant with feature-flagged agent skills. By the end, you'll have Datadog traces flowing into LaunchDarkly and a guarded rollout protecting a new AI skill.

---

## The application: Serenia Agent Skills

Serenia is an AI customer service agent for an event venue. When a customer sends a message, the agent classifies their intent and routes it to a specialized skill — each of which can be independently gated behind a LaunchDarkly feature flag.

### Agent architecture

```
Customer message
       ↓
┌──────────────────────────┐
│  Intent Detection        │  Claude classifies the message
│  (Claude Sonnet 4)       │  into: answer_faq, log_inquiry,
└──────────┬───────────────┘  qualify_lead, or auto_propose
           ↓
┌──────────────────────────┐
│  LaunchDarkly Flag Check │  Is the target skill enabled?
│  (per-user evaluation)   │  If not, fall back to a safe default
└──────────┬───────────────┘
           ↓
┌──────────────────────────┐
│  Skill Execution         │  Run the skill and trace the result
│  (traced with ddtrace)   │  with Datadog APM
└──────────┬───────────────┘
           ↓
    Response + metadata
```

### Skills

| Skill | Status | What it does |
|-------|--------|-------------|
| `answer_faq` | Stable | Answers venue questions using a knowledge base and Claude |
| `log_inquiry` | Stable | Records prospect contact info to Airtable |
| `qualify_lead` | New (flag-gated) | Scores leads as hot/warm/cold, determines follow-up action |
| `auto_propose` | Locked | Generates custom event proposals (coming soon) |

The `qualify_lead` skill is the one we'll guard — it's a heavier, higher-risk skill (more tokens, longer latency, larger hallucination surface) gated behind the `qualify-lead-skill` feature flag. If the flag is off, the agent falls back to `log_inquiry`. This is exactly the kind of progressive rollout that Guarded Rollouts is designed for.

### The UI

The frontend is a split-screen Next.js application:

- **Left panel** — Chat interface where customers interact with the agent
- **Right panel** — Real-time activity dashboard showing intent detection, skill routing, flag evaluations, lead scores, and latency for every message

<!-- TODO: Screenshot — The Serenia UI showing both panels: a chat conversation on the left and the activity dashboard on the right with routing, flag, and scoring details -->
![Serenia UI overview](screenshots/serenia-ui-overview.png)

### Observability pipeline

Every request generates APM trace data: auto-instrumented Anthropic SDK calls, custom skill execution spans, and feature flag evaluations tagged onto active spans. With dual shipping configured on the Datadog Agent, this data flows to both Datadog and LaunchDarkly — no additional instrumentation needed.

```
                              ┌──────────────────┐
                         ┌───▶│  LaunchDarkly     │
                         │    │  Observability    │
┌──────────────────┐     │    └──────────────────┘
│  Serenia App     │─────┤            │
│  (ddtrace +      │     │     Guarded Rollouts
│   LD SDK hook)   │     │    (auto-detect regressions
└──────────────────┘     │     tied to flag changes)
         │ :8126         │
   ┌─────▼────────┐     │    ┌──────────────────┐
   │ Datadog Agent │─────┼───▶│  Datadog APM     │
   │ (forwarder)   │     │    │  (traces, spans) │
   └──────────────┘     │    └──────────────────┘
                         │
                    dual shipping
```

---

## Demo walkthrough

### Step 1: Clone the repo

```bash
git clone https://github.com/your-org/serenia-agent-skills.git
cd serenia-agent-skills
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### Step 2: Set up credentials

Copy the example environment file and fill in your keys:

```bash
cp .env.example .env
```

You'll need credentials from four services:

| Service | Variable(s) | Where to find it |
|---------|------------|-----------------|
| **Anthropic** | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) > API Keys |
| **LaunchDarkly** | `LD_SDK_KEY`, `LD_CLIENT_SIDE_ID` | Project Settings > Environments |
| **Datadog** | `DD_API_KEY`, `DD_SITE` | Organization Settings > API Keys |
| **Airtable** | `AIRTABLE_PAT`, `AIRTABLE_BASE_ID` | [airtable.com/create/tokens](https://airtable.com/create/tokens) |

Your `.env` should look like this:

```bash
ANTHROPIC_API_KEY=sk-ant-...
LD_SDK_KEY=sdk-...
LD_CLIENT_SIDE_ID=...
DD_API_KEY=...
DD_SITE=us5.datadoghq.com
DD_AGENT_HOST=localhost
AIRTABLE_PAT=pat...
AIRTABLE_BASE_ID=app...
```

#### Airtable setup

The `log_inquiry` and `qualify_lead` skills write lead data to Airtable. Create a **Leads** table with these fields:

| Field | Type | Description |
|-------|------|-------------|
| Name | Single line text | Customer's name |
| Email | Email | Customer's email address |
| Message | Long text | The customer's original message |
| Status | Single select | `"New"` or `"Qualified"` |
| Lead Score | Single select | `"Hot"`, `"Warm"`, or `"Cold"` |
| Lead Action | Single line text | `"Scheduled call"`, `"Sent brochure"`, or `"Sent product info"` |
| Qualification Reason | Long text | LLM-generated explanation of the score |

The base ID is in your Airtable URL: `airtable.com/<BASE_ID>/...`. Generate a Personal Access Token with `data.records:write` scope on your base.

Airtable is optional — if credentials aren't set, skills still run and return responses, they just skip the CRM write.

To verify your Airtable setup is correct, run this from the project root:

```bash
python -c "
from dotenv import load_dotenv
load_dotenv()
from serenia.skills.airtable_client import get_table

table = get_table('Leads')
if table is None:
    print('FAIL: Airtable not configured — check AIRTABLE_PAT and AIRTABLE_BASE_ID in .env')
else:
    try:
        record = table.create({
            'Name': 'Test User',
            'Email': 'test@example.com',
            'Message': 'Airtable connectivity test',
            'Status': 'New',
        })
        print(f'SUCCESS: Created test record {record[\"id\"]}')
        table.delete(record['id'])
        print('Cleaned up test record')
    except Exception as e:
        print(f'FAIL: {e}')
        print('Check that your Leads table has these fields:')
        print('  - Name (Single line text)')
        print('  - Email (Email)')
        print('  - Message (Long text)')
        print('  - Status (Single select with options: New, Qualified)')
        print('  - Lead Score (Single select with options: Hot, Warm, Cold)')
        print('  - Lead Action (Single line text)')
        print('  - Qualification Reason (Long text)')
"
```

<!-- TODO: Screenshot — Airtable Leads table showing records created by both log_inquiry (Status: New) and qualify_lead (Status: Qualified, with Lead Score and Lead Action populated) -->
![Airtable Leads table](screenshots/airtable-leads-table.png)

### Step 3: Run the app and verify Datadog traces

Before running the app, here's a quick walkthrough of how the Datadog instrumentation works in this codebase. If you're already familiar with `ddtrace`, skip ahead to [running the app](#run-the-app).

#### What is ddtrace?

[`ddtrace`](https://docs.datadoghq.com/tracing/trace_collection/dd_libraries/python/) is Datadog's Python tracing library. It automatically patches popular libraries — including the Anthropic SDK — to capture trace data without modifying your application code.

#### Layer 1: Auto-instrumentation

The entire tracing setup is a single function call at app startup:

```python
# server.py
from serenia.observability.tracing import init_tracing

tracer = init_tracing()  # called once on startup
```

Inside `init_tracing()`, the key line is `patch(anthropic=True)`:

```python
# serenia/observability/tracing.py
from ddtrace import tracer, patch

def init_tracing():
    os.environ.setdefault("DD_SERVICE", "serenia-agent")
    os.environ.setdefault("DD_ENV", "demo")
    os.environ.setdefault("DD_VERSION", "0.1.0")

    # Auto-instrument Anthropic SDK calls
    patch(anthropic=True)

    return tracer
```

That's it. With `patch(anthropic=True)`, every `client.messages.create()` call in the codebase is automatically traced — no decorators, no wrappers, no changes to skill code. The tracer automatically reads `DD_AGENT_HOST` from the environment and sends APM traces to the Datadog Agent on port 8126.

For example, the `answer_faq` skill just uses the Anthropic SDK normally:

```python
# serenia/skills/answer_faq.py
client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=400,
    system="You are Serenia, the AI assistant for an event venue...",
    messages=[{"role": "user", "content": question}],
)
```

`ddtrace` patches this call at import time, automatically capturing the model, latency, and request metadata — all visible in Datadog APM.

#### Layer 2: Skill-level spans

On top of the auto-instrumented LLM calls, each skill is wrapped in a custom span using the `trace_skill()` helper. This adds skill-specific context — like which skill ran, what the lead score was, or whether an Airtable write succeeded:

```python
# serenia/observability/tracing.py
from ddtrace import tracer

def trace_skill(skill_name: str, flag_key: str | None = None):
    span = tracer.trace(
        f"serenia.skill.{skill_name}",
        service="serenia-agent",
        resource=skill_name,
    )
    span.set_tag("skill.name", skill_name)
    if flag_key:
        span.set_tag("feature_flag.key", flag_key)
    return span
```

Skills use it as a context manager and add their own tags:

```python
# serenia/skills/qualify_lead.py
with trace_skill("qualify_lead", flag_key="qualify-lead-skill") as span:
    # ... LLM call and lead scoring ...
    span.set_tag("skill.lead_score", result.get("score"))
    span.set_tag("skill.lead_action", result.get("action"))
    span.set_tag("skill.airtable_record_id", record_id)
```

This creates a parent span that nests the auto-instrumented Anthropic call inside it — so in Datadog APM you see the skill span with the LLM call as a child, giving you both the business context (which skill, what score) and the LLM details (latency, model) in one trace.

#### Layer 3: Feature flag correlation

The final layer connects LaunchDarkly flag evaluations to Datadog spans via a custom SDK hook. This is covered in [Step 4](#step-4-create-the-qualify-lead-feature-flag).

#### Run the app

Start the application:

```bash
uvicorn server:app --reload --port 8000
```

Generate some traces by chatting with the agent — either through the UI at `http://localhost:3000` or via curl:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Do you host baby showers?"}'
```

Open **Datadog > APM > Traces** and filter by `service:serenia-agent`. You should see traces for each request — `anthropic.request` spans nested inside skill spans like `serenia.skill.answer_faq` or `serenia.skill.agent.handle_message`.

<!-- TODO: Screenshot — Datadog APM Traces view filtered by service:serenia-agent, showing traced requests with Anthropic spans -->
![Datadog APM traces](screenshots/dd-apm-traces.png)

This confirms your existing Datadog instrumentation is working. These are the same APM traces we'll forward to LaunchDarkly — no additional instrumentation needed.

### Step 4: Create the qualify-lead feature flag

The `qualify_lead` skill is already wired to check a LaunchDarkly flag before executing. You need to create the flag in your LaunchDarkly project so the skill can run before traces start flowing to LaunchDarkly.

Create a boolean flag called `qualify-lead-skill`:

<!-- TODO: Screenshot — LaunchDarkly flag creation screen or the flag detail page for qualify-lead-skill, showing it as a boolean flag -->
![qualify-lead-skill flag in LaunchDarkly](screenshots/ld-qualify-lead-flag.png)

The app's routing logic checks this flag for every message that matches the `qualify_lead` intent. When the flag is **on**, the skill runs and scores the lead. When it's **off**, the agent falls back to `log_inquiry` instead.

The LaunchDarkly SDK hook automatically tags each flag evaluation onto the active Datadog span:

```
feature_flag.key:             "qualify-lead-skill"
feature_flag.provider_name:   "LaunchDarkly"
feature_flag.result.value:    "true"
feature_flag.result.variant:  "0"
feature_flag.context.key:     "user-abc123"
```

<!-- TODO: Screenshot — Datadog APM trace detail view, expand a span's tags section to show the feature_flag.* tags populated by the hook -->
![Feature flag tags on a Datadog span](screenshots/dd-span-flag-tags.png)

### Step 5: Configure dual shipping to Datadog and LaunchDarkly

Now that the flag exists and you've verified traces in Datadog APM, we'll configure the Datadog Agent to forward those same APM traces to LaunchDarkly as well. This requires **zero changes to your application code** — you just update the agent's `datadog.yaml`.

With dual shipping, your existing Datadog APM visibility stays intact while LaunchDarkly receives the trace data it needs for Guarded Rollouts.

#### Install the Datadog Agent

If you don't already have the Datadog Agent installed, follow [Datadog's install guide](https://docs.datadoghq.com/agent/) for your platform. After install, confirm it's running:

```bash
sudo datadog-agent status
```

#### Configure dual shipping

Edit your `datadog.yaml` config file (typically at `/opt/datadog-agent/etc/datadog.yaml` on macOS or `/etc/datadog-agent/datadog.yaml` on Linux).

Your `api_key` and `site` should already be configured from your Datadog Agent installation. Add the following `apm_config` block to forward APM traces to both LaunchDarkly and Datadog:

```yaml
# /opt/datadog-agent/etc/datadog.yaml

apm_config:
  enabled: true
  # Primary: forward APM traces to LaunchDarkly
  apm_dd_url: https://datadog.observability.app.launchdarkly.com:8126
  # Secondary: continue sending traces to Datadog APM
  additional_endpoints:
    "https://trace.agent.<YOUR_DD_SITE>":
      - <your-dd-api-key>
```

Replace `<YOUR_DD_SITE>` with your Datadog site (e.g., `us5.datadoghq.com`, `datadoghq.com`, `datadoghq.eu`) and `<your-dd-api-key>` with your Datadog API key.

This only affects APM traces — general metrics and checks continue going to Datadog as normal. Do **not** set `dd_url` to the LaunchDarkly endpoint, as LaunchDarkly only accepts APM trace data.

> **LaunchDarkly only:** If you don't need traces in Datadog APM, you can omit `additional_endpoints` and only set `apm_dd_url`.

#### Restart the Agent

```bash
sudo datadog-agent restart
```

#### Set the LaunchDarkly project ID

Make sure your `.env` includes the `OTEL_RESOURCE_ATTRIBUTES` so LaunchDarkly can route traces to your project:

```bash
DD_AGENT_HOST=localhost
OTEL_RESOURCE_ATTRIBUTES=launchdarkly.project_id=YOUR_CLIENT_SIDE_ID
```

Replace `YOUR_CLIENT_SIDE_ID` with the value from **LaunchDarkly > Project Settings > Environments**.

#### Restart your app and verify

```bash
uvicorn server:app --reload --port 8000
```

Send a few messages, then check both dashboards:

- **Datadog APM > Traces** — you should still see `serenia-agent` traces as before
- **LaunchDarkly > Observe > Traces** — the same traces should now appear here, with span data including `anthropic.request`, `serenia.skill.qualify_lead`, and `serenia.skill.agent.handle_message`

<!-- TODO: Screenshot — LaunchDarkly Observe > Traces showing ingested APM traces from the Datadog Agent, with span names like serenia.skill.qualify_lead and anthropic.request -->
![LaunchDarkly Observability traces](screenshots/ld-observability-traces.png)

Your app sends traces to `localhost:8126` (the Datadog Agent), which forwards them to both LaunchDarkly and Datadog. Traces arrive with flag evaluation data already attached — so LaunchDarkly can immediately correlate performance with flag variations.

### Step 6: Choose a metric to trigger rollback

Before creating a guarded rollout, decide which metric LaunchDarkly should monitor to detect regressions. For an AI agent skill like `qualify_lead`, good candidates are:

| Metric | Why it matters for AI skills |
|--------|------------------------------|
| **Error rate** | Catches LLM API failures, JSON parse errors, Airtable write failures |
| **Latency (p95/p99)** | Detects when the heavier LLM call is taking too long — `qualify_lead` uses more tokens than `log_inquiry` |
| **Custom span tags** | Monitor `skill.lead_score` distribution — a sudden spike in `"cold"` scores could indicate a prompt regression |

Create the metric in LaunchDarkly by navigating to **Observability > Metrics** and defining a metric based on the trace data flowing in from the Datadog Agent.

<!-- TODO: Screenshot — LaunchDarkly Observability > Metrics creation screen, showing a metric being defined (e.g., error rate or p95 latency on the qualify_lead skill spans) -->
![Creating a metric in LaunchDarkly](screenshots/ld-create-metric.png)

### Step 7: Create the guarded rollout

Now tie it all together. On your `qualify-lead-skill` flag:

1. **Navigate to the flag** in LaunchDarkly
2. **Enable Guarded Rollouts** — attach the metric you created in Step 6
3. **Set your rollout strategy** — start with a percentage rollout (e.g., 10% of users get `qualify_lead`, 90% fall back to `log_inquiry`)
4. **Define the rollback threshold** — if the metric regresses beyond your threshold, LaunchDarkly will automatically roll back the flag

LaunchDarkly now monitors the trace metrics for each flag variation. If `qualify_lead` starts showing higher error rates or latency spikes compared to the `log_inquiry` fallback, the guarded rollout catches it and rolls back — before the regression reaches all users.

<!-- TODO: Screenshot — LaunchDarkly flag detail page with Guarded Rollouts enabled, showing the attached metric and rollout percentage -->
![Guarded Rollouts enabled on qualify-lead-skill](screenshots/ld-guarded-rollouts-enabled.png)

### Step 8: Verify the guarded rollout

Generate traffic to exercise both flag variations:

```bash
# Send several lead-qualifying messages
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d "{\"message\": \"Hi, I'm planning a wedding for 150 guests on June 15th. My budget is around \\$3000. Can I schedule a tour?\", \"context_key\": \"user-$i\"}" &
done
wait
```

Check that:

1. **Traces are flowing to both** — Datadog APM and LaunchDarkly Observe > Traces both show `serenia-agent` traces with `feature_flag.key: qualify-lead-skill`
2. **Flag correlation is working** — LaunchDarkly traces show spans correlated with flag evaluations
3. **The guarded rollout is monitoring** — the flag page shows metric data for each variation

<!-- TODO: Screenshot — LaunchDarkly Guarded Rollouts monitoring view showing metric data for both flag variations (qualify_lead vs log_inquiry fallback), confirming the rollout is active and tracking -->
![Guarded Rollouts monitoring](screenshots/ld-guarded-rollouts-monitoring.png)

If the `qualify_lead` variation causes a regression, LaunchDarkly will flag it and prompt a rollback — closing the loop between feature flags and observability.

<!-- TODO: Screenshot — LaunchDarkly Guarded Rollouts detecting a regression and showing the rollback prompt or automatic rollback notification -->
![Guarded Rollouts regression detection](screenshots/ld-guarded-rollouts-regression.png)

---

## Further reading

- [LaunchDarkly Observability: Datadog Agent](https://launchdarkly.com/docs/home/observability/datadog-agent)
- [LaunchDarkly Guarded Rollouts](https://launchdarkly.com/docs/home/releases/guarded-rollouts)
- [Datadog LLM Observability](https://docs.datadoghq.com/llm_observability/)
- [Datadog Agent Dual Shipping](https://docs.datadoghq.com/agent/configuration/dual-shipping/)
