"""Synthetic traffic driver for the Serenia agent.

Fires a batch of varied lead-qualification inquiries against a running Serenia
FastAPI server (default: http://localhost:8000). Used to generate enough
volume to cross the `serenia-skill-error-rate` alert threshold once a buggy
AI Config variation is rolled to a fraction of traffic.

Usage:
    # Defaults: 30 requests, 3 in flight, http://localhost:8000
    python scripts/drive_traffic.py

    # Tune
    python scripts/drive_traffic.py --requests 60 --concurrency 5 --base-url http://localhost:8000

Each request gets a unique context_key so LaunchDarkly's percentage rollouts
distribute it across variations. Outcomes are logged one line per request.
The script is safely re-runnable; nothing is persisted locally.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

load_dotenv()


INQUIRIES = [
    "Hi, I'm Dana Rivera (dana@riveraphotography.com). I'm planning my wedding reception "
    "for September 20th, expecting 120 guests. We'd need full catering and decor. Budget around $8k. "
    "Can we schedule a tour this week?",

    "Hello — my name is Marcus Chen, marcus@greenleafplants.co. We're doing a corporate offsite "
    "for ~40 people on October 14th. Need AV, breakfast, and lunch catering. What's your availability?",

    "Hi! I'm Priya Sharma (priya.sharma@example.com). Booking a 50th birthday party for my mom on "
    "November 8th, about 75 guests. Looking at your decor packages and bar service. Can someone call me?",

    "We're throwing a baby shower on June 28th for around 30 guests. I'd love to come tour the space "
    "this weekend if possible. — Olivia, olivia@example.com",

    "Hey, this is James from Halberd & Co. We need a venue for a product launch on August 5th, "
    "150 guests, full AV, passed apps and bar. james@halberd.example. Can you put together pricing?",

    "I'm planning my wedding reception for July 12th — 100 guests, need full catering, would love a "
    "weekend tour. Reachable at alex.morgan@example.com.",

    "Hi I'm Sam (sam@cloudwave.io). We're running a 2-day corporate retreat in March for 60 people. "
    "Need lodging recs nearby plus full catering and breakout rooms. When can we tour?",

    "Looking to book your space for a 25th anniversary dinner on Sept 7th — about 50 guests, "
    "full catering, decor setup. My budget is around $5k. Email me at noah.parker@example.com.",

    "Hi! Booking a quinceañera for my daughter on May 18th — expecting around 140 guests, full bar, "
    "DJ, decor. Contact me at maria.santos@example.com to schedule a visit.",

    "We're hosting a nonprofit fundraiser gala on November 22nd, ~200 guests, formal seated dinner, "
    "stage and AV. Please reach out — events@helpinghandsfoundation.example.",

    "Hi, planning a corporate holiday party for our team of 80 on December 14th. Want passed apps, "
    "full bar, and DJ. Reachable at zoe.kim@launchpad.example.",

    "Hello, this is Diego Alvarez (diego@alvarezarch.example). Hosting a 75-person engagement party "
    "August 24th. Need catering and decor. Can we tour next Tuesday?",

    "Booking a bridal shower on April 19th, 40 guests, brunch catering and floral package. "
    "Email me at hannah.lee@example.com — would love a tour this Saturday.",

    "I'm reaching out about your venue for a 30th birthday on June 7th, ~60 guests, full bar, DJ. "
    "Contact: priya.kapoor@example.com.",

    "Hi! We're a startup planning an investor reception for 90 guests on October 2nd. Need AV, bar, "
    "passed hors d'oeuvres. Please call me at rachel.tan@series-b.example.",

    "Hosting a retirement party for my dad on September 15th — about 55 guests, full catering. "
    "Budget around $4k. Reach me at owen.fitzgerald@example.com.",

    "Hello, planning a wedding reception on April 27th for 130 guests. Looking at your premium "
    "package with decor + catering. Contact: lina.romero@example.com — can we schedule a tour?",

    "Hi, I'm hosting a tech meetup for ~70 attendees on July 23rd. Need projector, bar, light apps. "
    "Reach me at devon.brooks@stackful.example.",

    "We're planning a Sweet 16 for my daughter on May 4th, around 80 guests, full DJ + decor. "
    "Email: amelia.wong@example.com. Tour availability?",

    "Hello — booking a corporate training day for 35 people on February 11th, full breakfast and lunch. "
    "Reach me at trent.becker@firmly.example.",

    "Reaching out about your venue for a 40-person rehearsal dinner on June 6th, plated dinner. "
    "Contact: jenna.kowalski@example.com.",

    "Hi! Planning a holiday-themed corporate event for ~110 staff on December 6th, full bar, DJ, decor. "
    "Email pat@northstar-it.example.",

    "Booking a milestone birthday for 65 guests on August 16th, mixed cocktail-and-dinner format. "
    "Reach me at ines.mendoza@example.com.",

    "We'd like to host our annual charity auction at your venue on October 25th, 180 guests. "
    "Need stage, AV, dinner service. — Aaron, board@cityhopecharity.example.",

    "Hi, planning a wedding ceremony AND reception on May 30th — 95 guests, full catering, ceremony "
    "setup, reception decor. Tour availability this weekend? sofia.davies@example.com.",

    "Hello, I'm Reza (reza.haidari@example.com). Booking a 50-person corporate dinner on Nov 1st, "
    "private dining setup with wine pairings. When can we tour?",

    "Reaching out about your venue for a Diwali celebration on October 18th, ~120 guests. Need full "
    "catering and decor. Contact: anita.iyer@example.com.",

    "Hi, planning a memorial reception for my late grandmother on March 15th, 60 guests, simple lunch "
    "service. Reach me at colin.murphy@example.com.",

    "Hosting a launch event for our new product line on September 9th — 100 guests, AV-heavy, passed "
    "apps and bar. Email: events@verdantcosmetics.example.",

    "Hi! Booking a graduation party for my son on June 22nd, ~85 guests, full catering and DJ. "
    "Reach me at karen.oboyle@example.com.",

    "We need a venue for a corporate hackathon kickoff on January 24th, 70 attendees, breakfast and "
    "lunch service, AV. Contact: ravi.patel@buildhouse.example.",

    "Hello, planning a 35-person wedding rehearsal dinner on August 9th, plated dinner with wine. "
    "Email me at ginny.thompson@example.com.",
]


@dataclass
class CallResult:
    idx: int
    context_key: str
    status_code: int | None
    routed_to: str | None
    lead_score: str | None
    latency_ms: int | None
    error: str | None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Drive synthetic traffic at the Serenia agent.")
    p.add_argument("--base-url", default=os.environ.get("SERENIA_BASE_URL", "http://localhost:8000"))
    p.add_argument("--requests", type=int, default=30, help="Total number of requests to send.")
    p.add_argument("--concurrency", type=int, default=3, help="Max in-flight requests.")
    p.add_argument("--seed", type=int, default=None, help="Optional RNG seed for reproducible runs.")
    p.add_argument("--timeout", type=float, default=60.0, help="Per-request timeout in seconds.")
    return p.parse_args()


async def send_one(
    client: httpx.AsyncClient,
    base_url: str,
    idx: int,
    inquiry: str,
    timeout: float,
) -> CallResult:
    context_key = f"driver-{uuid.uuid4().hex[:8]}"
    url = f"{base_url.rstrip('/')}/api/chat"
    started = time.monotonic()
    try:
        resp = await client.post(
            url,
            json={"message": inquiry, "context_key": context_key},
            timeout=timeout,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code >= 400:
            return CallResult(
                idx=idx,
                context_key=context_key,
                status_code=resp.status_code,
                routed_to=None,
                lead_score=None,
                latency_ms=elapsed_ms,
                error=resp.text[:200],
            )
        body = resp.json()
        meta = body.get("metadata") or {}
        return CallResult(
            idx=idx,
            context_key=context_key,
            status_code=resp.status_code,
            routed_to=meta.get("routed_to"),
            lead_score=meta.get("lead_score"),
            latency_ms=meta.get("latency_ms") or elapsed_ms,
            error=None,
        )
    except Exception as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return CallResult(
            idx=idx,
            context_key=context_key,
            status_code=None,
            routed_to=None,
            lead_score=None,
            latency_ms=elapsed_ms,
            error=f"{type(e).__name__}: {e}",
        )


async def run(args: argparse.Namespace) -> int:
    if args.seed is not None:
        random.seed(args.seed)

    inquiries = [random.choice(INQUIRIES) for _ in range(args.requests)]
    sem = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient() as client:
        async def gated(idx: int, inquiry: str) -> CallResult:
            async with sem:
                return await send_one(client, args.base_url, idx, inquiry, args.timeout)

        print(
            f"[driver] Sending {args.requests} requests to {args.base_url} "
            f"(concurrency={args.concurrency})"
        )
        run_started = time.monotonic()
        tasks = [asyncio.create_task(gated(i, q)) for i, q in enumerate(inquiries)]
        results: list[CallResult] = []
        for coro in asyncio.as_completed(tasks):
            r = await coro
            results.append(r)
            tag = (
                f"OK  routed={r.routed_to or '-':<13} score={r.lead_score or '-':<6}"
                if r.error is None
                else f"ERR {r.error[:120]}"
            )
            print(
                f"[driver] req#{r.idx:03d} ctx={r.context_key} "
                f"http={r.status_code or 'n/a'} {r.latency_ms}ms  {tag}"
            )

    total_ms = int((time.monotonic() - run_started) * 1000)
    ok = sum(1 for r in results if r.error is None and (r.status_code or 0) < 400)
    err = len(results) - ok
    by_route: dict[str, int] = {}
    for r in results:
        key = r.routed_to or ("error" if r.error else "unknown")
        by_route[key] = by_route.get(key, 0) + 1

    print()
    print(f"[driver] Done in {total_ms}ms. ok={ok} err={err} total={len(results)}")
    print(f"[driver] Routing breakdown: {by_route}")
    return 0 if err == 0 else 1


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n[driver] Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
