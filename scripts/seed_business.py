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


VERTICAL_SERVICES: dict[str, list[dict[str, Any]]] = {
    "plumbing": [
        {"name": "Drain Cleaning", "description": "Unclog drains and clear blockages", "base_price_low": 150, "base_price_high": 350, "required_intake_fields": ["name", "address", "scope"], "tags": ["plumbing", "drain"]},
        {"name": "Pipe Repair", "description": "Fix leaking or burst pipes", "base_price_low": 200, "base_price_high": 800, "required_intake_fields": ["name", "address", "scope", "urgency"], "tags": ["plumbing", "repair"]},
        {"name": "Water Heater Install", "description": "Install or replace water heater", "base_price_low": 800, "base_price_high": 2500, "required_intake_fields": ["name", "address", "heater_type"], "tags": ["plumbing", "install"]},
        {"name": "Toilet Repair", "description": "Fix running or clogged toilets", "base_price_low": 100, "base_price_high": 300, "required_intake_fields": ["name", "address"], "tags": ["plumbing"]},
    ],
    "hvac": [
        {"name": "AC Repair", "description": "Diagnose and repair air conditioning", "base_price_low": 150, "base_price_high": 600, "required_intake_fields": ["name", "address", "unit_type"], "tags": ["hvac", "repair"]},
        {"name": "Furnace Tune-Up", "description": "Annual furnace maintenance", "base_price_low": 80, "base_price_high": 150, "required_intake_fields": ["name", "address"], "tags": ["hvac", "maintenance"]},
        {"name": "AC Install", "description": "New AC unit installation", "base_price_low": 3000, "base_price_high": 8000, "required_intake_fields": ["name", "address", "sqft", "unit_preference"], "tags": ["hvac", "install"]},
        {"name": "Duct Cleaning", "description": "Clean air ducts and vents", "base_price_low": 200, "base_price_high": 500, "required_intake_fields": ["name", "address", "sqft"], "tags": ["hvac", "cleaning"]},
    ],
    "electrical": [
        {"name": "Outlet Install", "description": "Install new electrical outlets", "base_price_low": 100, "base_price_high": 300, "required_intake_fields": ["name", "address", "quantity"], "tags": ["electrical"]},
        {"name": "Panel Upgrade", "description": "Upgrade electrical panel", "base_price_low": 1500, "base_price_high": 4000, "required_intake_fields": ["name", "address", "current_amps"], "tags": ["electrical", "upgrade"]},
        {"name": "Lighting Install", "description": "Install or replace lighting fixtures", "base_price_low": 100, "base_price_high": 500, "required_intake_fields": ["name", "address", "fixture_count"], "tags": ["electrical", "lighting"]},
    ],
    "landscaping": [
        {"name": "Lawn Mowing", "description": "Weekly or bi-weekly lawn mowing", "base_price_low": 30, "base_price_high": 100, "required_intake_fields": ["name", "address", "lot_size"], "tags": ["landscaping", "recurring"]},
        {"name": "Tree Trimming", "description": "Trim and shape trees", "base_price_low": 200, "base_price_high": 1500, "required_intake_fields": ["name", "address", "tree_count", "tree_size"], "tags": ["landscaping", "trees"]},
        {"name": "Landscape Design", "description": "Custom landscape design and install", "base_price_low": 1000, "base_price_high": 10000, "required_intake_fields": ["name", "address", "scope", "budget"], "tags": ["landscaping", "design"]},
        {"name": "Irrigation Install", "description": "Sprinkler system install or repair", "base_price_low": 500, "base_price_high": 3000, "required_intake_fields": ["name", "address", "lot_size"], "tags": ["landscaping", "irrigation"]},
    ],
    "cleaning": [
        {"name": "Standard Clean", "description": "Regular residential cleaning", "base_price_low": 100, "base_price_high": 250, "required_intake_fields": ["name", "address", "sqft", "bedrooms"], "tags": ["cleaning"]},
        {"name": "Deep Clean", "description": "Thorough deep cleaning", "base_price_low": 200, "base_price_high": 500, "required_intake_fields": ["name", "address", "sqft", "bedrooms"], "tags": ["cleaning", "deep"]},
        {"name": "Move-In/Out Clean", "description": "Cleaning for moving transitions", "base_price_low": 250, "base_price_high": 600, "required_intake_fields": ["name", "address", "sqft"], "tags": ["cleaning", "move"]},
    ],
    "dental": [
        {"name": "Cleaning & Exam", "description": "Routine dental cleaning and examination", "base_price_low": 100, "base_price_high": 300, "required_intake_fields": ["name", "phone", "insurance_info"], "tags": ["dental", "preventive"]},
        {"name": "Filling", "description": "Dental filling for cavities", "base_price_low": 150, "base_price_high": 400, "required_intake_fields": ["name", "phone", "tooth_location"], "tags": ["dental", "restorative"]},
        {"name": "Whitening", "description": "Professional teeth whitening", "base_price_low": 200, "base_price_high": 600, "required_intake_fields": ["name", "phone"], "tags": ["dental", "cosmetic"]},
        {"name": "Emergency Visit", "description": "Urgent dental care", "base_price_low": 200, "base_price_high": 500, "required_intake_fields": ["name", "phone", "description"], "tags": ["dental", "emergency"]},
    ],
    "general": [
        {"name": "Consultation", "description": "Initial consultation", "base_price_low": 0, "base_price_high": 100, "required_intake_fields": ["name", "phone"], "tags": ["general"]},
        {"name": "Standard Service", "description": "Standard service visit", "base_price_low": 100, "base_price_high": 500, "required_intake_fields": ["name", "address", "scope"], "tags": ["general"]},
    ],
}


def _seed_services(client: Client, business_id: str, vertical: str) -> int:
    presets = VERTICAL_SERVICES.get(vertical, VERTICAL_SERVICES["general"])
    count = 0
    for svc in presets:
        existing = (
            client.table("services")
            .select("id")
            .eq("business_id", business_id)
            .eq("name", svc["name"])
            .limit(1)
            .execute()
        )
        if existing.data:
            continue
        client.table("services").insert({**svc, "business_id": business_id, "active": True}).execute()
        count += 1
    return count


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
    parser.add_argument(
        "--services",
        default=None,
        help="Seed vertical services (plumbing, hvac, electrical, landscaping, cleaning, dental, general)",
    )
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

    services_seeded = 0
    if args.services:
        services_seeded = _seed_services(client, business["id"], args.services)

    print(
        json.dumps(
            {
                "business_id": business["id"],
                "business_slug": business["slug"],
                "did": args.did,
                "phone_channel_id": phone_channel["id"],
                "sms_channel_id": sms_channel["id"],
                "services_seeded": services_seeded,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
