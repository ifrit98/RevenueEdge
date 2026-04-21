#!/usr/bin/env python3
"""Seed a test business + phone/SMS channel for Phase 1 smoke tests.

Usage (run from the repo root after `source .venv/bin/activate`):

    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... \
      python scripts/seed_business.py \
        --name "Smoke Test Plumbing" \
        --slug smoke-test-plumbing \
        --did +15555550123

Outputs the created (or pre-existing) business_id + channel_ids as JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

try:
    from supabase import Client, create_client
except ImportError:
    sys.stderr.write("supabase package not installed. Run scripts/bootstrap.sh first.\n")
    sys.exit(1)


def _client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY (or SUPABASE_SERVICE_ROLE) must be set")
    return create_client(url, key)


def _upsert_business(client: Client, *, name: str, slug: str, vertical: str, timezone: str) -> dict:
    existing = (
        client.table("businesses").select("*").eq("slug", slug).limit(1).execute()
    )
    rows = existing.data or []
    if rows:
        return rows[0]
    res = (
        client.table("businesses")
        .insert(
            {
                "name": name,
                "slug": slug,
                "vertical": vertical,
                "timezone": timezone,
                "status": "active",
            }
        )
        .execute()
    )
    if not res.data:
        raise SystemExit("Failed to insert business")
    return res.data[0]


def _ensure_channel(
    client: Client,
    *,
    business_id: str,
    channel_type: str,
    provider: str,
    external_id: str,
    display_name: Optional[str],
    config: dict[str, Any],
) -> dict:
    q = (
        client.table("channels")
        .select("*")
        .eq("business_id", business_id)
        .eq("channel_type", channel_type)
        .eq("provider", provider)
        .eq("external_id", external_id)
        .limit(1)
        .execute()
    )
    rows = q.data or []
    if rows:
        return rows[0]
    res = (
        client.table("channels")
        .insert(
            {
                "business_id": business_id,
                "channel_type": channel_type,
                "provider": provider,
                "external_id": external_id,
                "display_name": display_name,
                "status": "active",
                "config": config,
            }
        )
        .execute()
    )
    if not res.data:
        raise SystemExit(f"Failed to insert channel {channel_type}")
    return res.data[0]


def _run_seed_defaults(client: Client, business_id: str) -> None:
    try:
        client.rpc("seed_revenue_edge_mvp_defaults", {"p_business_id": business_id}).execute()
    except Exception as exc:
        sys.stderr.write(f"WARN: seed_revenue_edge_mvp_defaults failed (continuing): {exc}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="Smoke Test Plumbing")
    parser.add_argument("--slug", default="smoke-test-plumbing")
    parser.add_argument("--vertical", default="service_pro")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--did", default="+15555550123", help="Primary DID for voice + SMS")
    parser.add_argument("--provider", default="retell")
    parser.add_argument("--skip-defaults", action="store_true")
    args = parser.parse_args()

    client = _client()
    business = _upsert_business(
        client,
        name=args.name,
        slug=args.slug,
        vertical=args.vertical,
        timezone=args.timezone,
    )

    phone_channel = _ensure_channel(
        client,
        business_id=business["id"],
        channel_type="phone",
        provider=args.provider,
        external_id=args.did,
        display_name=f"Inbound Voice ({args.did})",
        config={"from_number": args.did},
    )
    sms_channel = _ensure_channel(
        client,
        business_id=business["id"],
        channel_type="sms",
        provider=args.provider,
        external_id=args.did,
        display_name=f"Inbound SMS ({args.did})",
        config={"from_number": args.did},
    )

    if not args.skip_defaults:
        _run_seed_defaults(client, business["id"])

    print(
        json.dumps(
            {
                "business_id": business["id"],
                "business_slug": business["slug"],
                "did": args.did,
                "phone_channel_id": phone_channel["id"],
                "sms_channel_id": sms_channel["id"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
