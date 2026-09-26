#!/usr/bin/env python3
"""Send mixed chat traffic to a running Serenia API.

Qualify-lead messages include an event type, a guest count, and a specific
date so intent detection can route them to ``qualify_lead``. When the
``qualify-lead-skill`` flag is on for that user, the skill evaluates AI Config
``qualify-lead-config`` and records ``$ld:ai:generation:success`` (metric
``ld_autogen__ai-completion-success``). FAQ and log-inquiry messages exercise
the other routes and do not emit that completion event.

The script only calls HTTP. API keys stay in the server process (``.env``);
do not put secrets in this file or its arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


CHAT_PATH = "/api/chat"


@dataclass(frozen=True)
class TrafficMessage:
    """One canned customer message and the LaunchDarkly user it represents."""

    kind: str
    context_key: str
    message: str


# Several qualify-lead notes first so a short run still produces completion
# events. FAQ and inquiry messages are included so routing is not only the
# flagged path. Wording follows the intent rules in serenia/agent.py:
# qualify_lead needs concrete booking details, log_inquiry is contact info
# without those details, and answer_faq has neither contact info nor a
# request for a quote/proposal.
MESSAGE_MIX: tuple[TrafficMessage, ...] = (
    TrafficMessage(
        "qualify_lead",
        "customer-dana",
        "Hi, I'm Dana Rivera (dana@riveraphotography.com). I'm planning my wedding "
        "reception for September 20th — expecting about 120 guests. We'd need the full "
        "catering package and decor setup. Budget is around $8k total. Can we schedule "
        "a tour this week?",
    ),
    TrafficMessage(
        "qualify_lead",
        "customer-jordan",
        "I'm Jordan Hale, jordan@northwind.io. We want to book a corporate holiday party "
        "on December 12 for about 80 guests, with in-house catering and a small AV setup. "
        "Can we tour the room next Tuesday?",
    ),
    TrafficMessage(
        "qualify_lead",
        "customer-sam",
        "Hi, I'm Sam Okonkwo (sam.okonkwo@gmail.com). I'm hosting a baby shower on April 18 "
        "for 40 guests and would like the decor package. Is the afternoon of the 18th available?",
    ),
    TrafficMessage(
        "qualify_lead",
        "customer-riley",
        "This is Riley Nguyen, riley.nguyen@brightpath.co. We're planning a birthday dinner "
        "on November 8 for 25 guests and want in-house catering. Could we visit this Thursday?",
    ),
    TrafficMessage(
        "qualify_lead",
        "customer-elena",
        "I'm Elena Vasquez (elena@vasquezstudio.com). I need the space for a private dinner "
        "on June 6 with 30 guests, plus bar service. We'd like to book a walkthrough this week.",
    ),
    TrafficMessage(
        "answer_faq",
        "customer-olivia",
        "Hi! What types of events do you host? I'm thinking about a baby shower.",
    ),
    TrafficMessage(
        "answer_faq",
        "customer-james",
        "Do you allow outside caterers? And is there parking for about 50 cars?",
    ),
    TrafficMessage(
        "log_inquiry",
        "customer-marcus",
        "Hey, I'm Marcus Chen, marcus@greenleafplants.co. We're looking for a space "
        "for our team events. Can someone reach out to tell us more?",
    ),
    TrafficMessage(
        "log_inquiry",
        "customer-nina",
        "Hi, my name is Nina Patel and my email is nina.patel@example.com. "
        "I'm interested in the venue — please have someone contact me.",
    ),
)


def build_plan(
    messages: tuple[TrafficMessage, ...] | list[TrafficMessage],
    *,
    count: int | None,
    repeats: int,
    context_key: str | None,
) -> list[TrafficMessage]:
    """Expand the catalog into the requests that will be sent.

    ``count`` is a total and cycles the catalog. Otherwise the full catalog is
    repeated ``repeats`` times. A context key replaces every per-message key.
    """
    catalog = list(messages)
    if not catalog:
        return []
    if count is not None:
        selected = [catalog[i % len(catalog)] for i in range(count)]
    else:
        selected = catalog * repeats
    if context_key:
        selected = [
            TrafficMessage(item.kind, context_key, item.message) for item in selected
        ]
    return selected


def post_chat(
    base_url: str,
    message: str,
    context_key: str,
    timeout: float,
) -> tuple[int, object]:
    """POST one chat turn. Returns the HTTP status and a parsed JSON body when possible."""
    url = base_url.rstrip("/") + CHAT_PATH
    payload = json.dumps({"message": message, "context_key": context_key}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "serenia-seed-traffic/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _decode_body(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode_body(exc.read())
    except urllib.error.URLError as exc:
        reason = exc.reason
        raise ConnectionError(str(getattr(reason, "strerror", reason))) from exc


def _decode_body(raw: bytes) -> object:
    text = raw.decode("utf-8", errors="replace")
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def format_result(status: int, body: object) -> str:
    """One-line description of a chat response, including the route the agent chose."""
    if not isinstance(body, dict):
        text = body if isinstance(body, str) else json.dumps(body)
        collapsed = " ".join(text.split())
        if len(collapsed) > 240:
            collapsed = collapsed[:237] + "..."
        return f"HTTP {status}  {collapsed}"

    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    parts = [f"HTTP {status}"]
    routed_to = metadata.get("routed_to")
    detected = metadata.get("detected_intent")
    if routed_to:
        parts.append(f"routed_to={routed_to}")
    if detected and detected != routed_to:
        parts.append(f"intent={detected}")
    if metadata.get("flag_evaluated") is not None:
        parts.append(f"flag={metadata.get('flag_evaluated')}={metadata.get('flag_result')}")
    if metadata.get("fallback"):
        parts.append("fallback=true")
    if metadata.get("lead_score"):
        parts.append(f"score={metadata['lead_score']}")
    if metadata.get("lead_action"):
        parts.append(f"action={metadata['lead_action']}")
    if metadata.get("latency_ms") is not None:
        parts.append(f"{metadata['latency_ms']}ms")
    if status >= 400:
        detail = body.get("detail")
        if detail:
            detail_text = detail if isinstance(detail, str) else json.dumps(detail)
            collapsed = " ".join(detail_text.split())
            if len(collapsed) > 180:
                collapsed = collapsed[:177] + "..."
            parts.append(collapsed)
    return "  ".join(parts)


def _preview(message: str, limit: int = 88) -> str:
    collapsed = " ".join(message.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Post a mix of qualify-lead, FAQ, and inquiry messages to the local "
            "Serenia /api/chat endpoint so LaunchDarkly completion events have traffic."
        ),
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Serenia API origin (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Total /api/chat requests to send, cycling the message mix. Overrides --repeats.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="How many times to send the full message mix (default: 1). Ignored when --count is set.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds to wait between requests (default: 0)",
    )
    parser.add_argument(
        "--context-key",
        default=None,
        help=(
            "LaunchDarkly user key sent as context_key on every request. "
            "Omit to rotate the built-in keys (customer-dana, customer-marcus, ...) "
            "so a percentage rollout sees more than one user."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-request timeout in seconds (default: 120). Model calls need the headroom.",
    )
    args = parser.parse_args(argv)
    base_url = args.base_url.strip().rstrip("/")
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        parser.error("--base-url must start with http:// or https://")
    args.base_url = base_url
    if args.count is not None and args.count < 1:
        parser.error("--count must be at least 1")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.delay < 0:
        parser.error("--delay must be zero or greater")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if args.context_key is not None:
        args.context_key = args.context_key.strip() or None
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    planned = build_plan(
        MESSAGE_MIX,
        count=args.count,
        repeats=args.repeats,
        context_key=args.context_key,
    )
    total = len(planned)
    kinds = {}
    for item in planned:
        kinds[item.kind] = kinds.get(item.kind, 0) + 1
    mix = ", ".join(f"{name}={count}" for name, count in kinds.items())
    print(f"Sending {total} request(s) to {args.base_url}{CHAT_PATH} ({mix})")
    if args.context_key:
        print(f"LaunchDarkly user key: {args.context_key}")
    else:
        print("LaunchDarkly user key: rotating per message")

    ok = 0
    failed = 0
    routed: dict[str, int] = {}
    for index, item in enumerate(planned, start=1):
        if index > 1 and args.delay:
            time.sleep(args.delay)
        print(f"[{index}/{total}] {item.kind}  context={item.context_key}")
        print(f"         {_preview(item.message)}")
        try:
            status, body = post_chat(
                args.base_url,
                item.message,
                item.context_key,
                args.timeout,
            )
        except (ConnectionError, TimeoutError) as exc:
            print(f"         could not reach {args.base_url}{CHAT_PATH}: {exc}")
            print("Start the API first: uvicorn server:app --reload --port 8000")
            return 1
        print(f"         {format_result(status, body)}")
        if 200 <= status < 300:
            ok += 1
            metadata = body.get("metadata") if isinstance(body, dict) else None
            route = metadata.get("routed_to") if isinstance(metadata, dict) else None
            if route:
                routed[route] = routed.get(route, 0) + 1
        else:
            failed += 1

    print(f"sent={total} ok={ok} failed={failed}")
    if routed:
        breakdown = ", ".join(f"{name}={count}" for name, count in sorted(routed.items()))
        print(f"routed: {breakdown}")
    if failed:
        print(
            "Qualify-lead turns record $ld:ai:generation:success only when the server "
            "has ANTHROPIC_API_KEY and LD_SDK_KEY set and qualify-lead-skill is on "
            "for that context key. Leave the API running about 5 seconds so the SDK flushes."
        )
        return 1
    print(
        "Leave the API running about 5 seconds (or stop it cleanly) so the LaunchDarkly "
        "SDK flushes $ld:ai:generation:success. That event is recorded only for turns "
        "that actually ran qualify_lead."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
