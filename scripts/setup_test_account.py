#!/usr/bin/env python3
"""Set up a complete test account for end-to-end manual testing.

Creates:
  1. A Supabase Auth user (email/password)
  2. A test business with channels and default templates/rules/workflows
  3. A business_members row linking the user to the business as owner
  4. Optional vertical-specific service presets

After running this, you can log in to the dashboard and see real data.

Usage:
    source .venv-re/bin/activate
    source .env
    python scripts/setup_test_account.py

    # Or with custom options:
    python scripts/setup_test_account.py \
      --email test@example.com \
      --password testing123 \
      --business-name "Acme Plumbing" \
      --vertical home_services \
      --services plumbing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
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
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return create_client(url, key)


def _create_auth_user(email: str, password: str) -> str:
    """Create a Supabase Auth user via the Admin API. Returns user_id."""
    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE")

    req = urllib.request.Request(
        f"{url}/auth/v1/admin/users",
        data=json.dumps({
            "email": email,
            "password": password,
            "email_confirm": True,
        }).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "apikey": key,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["id"]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if "already been registered" in body or "already_exists" in body:
            return _find_existing_user(email)
        raise SystemExit(f"Failed to create auth user: {exc.code} {body}")


def _find_existing_user(email: str) -> str:
    """Look up an existing Auth user by email."""
    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE")

    req = urllib.request.Request(
        f"{url}/auth/v1/admin/users?page=1&per_page=50",
        headers={
            "Authorization": f"Bearer {key}",
            "apikey": key,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
        users = data if isinstance(data, list) else data.get("users", [])
        for u in users:
            if u.get("email", "").lower() == email.lower():
                return u["id"]
    raise SystemExit(f"User {email} exists but could not be found via admin API")


def _upsert_business(client: Client, *, name: str, slug: str, vertical: str, timezone: str) -> dict:
    existing = client.table("businesses").select("*").eq("slug", slug).limit(1).execute()
    if existing.data:
        return existing.data[0]
    res = client.table("businesses").insert({
        "name": name,
        "slug": slug,
        "vertical": vertical,
        "timezone": timezone,
        "status": "active",
        "hours": {
            "monday": {"open": "08:00", "close": "18:00"},
            "tuesday": {"open": "08:00", "close": "18:00"},
            "wednesday": {"open": "08:00", "close": "18:00"},
            "thursday": {"open": "08:00", "close": "18:00"},
            "friday": {"open": "08:00", "close": "18:00"},
            "saturday": {"open": "09:00", "close": "14:00"},
            "sunday": None,
        },
        "escalation": {
            "low_confidence_threshold": 0.4,
            "always_escalate_intents": ["complaint", "legal_threat"],
        },
    }).execute()
    if not res.data:
        raise SystemExit("Failed to insert business")
    return res.data[0]


def _ensure_channel(client: Client, *, business_id: str, channel_type: str,
                    provider: str, external_id: str, display_name: str, config: dict) -> dict:
    q = (client.table("channels").select("*")
         .eq("business_id", business_id)
         .eq("channel_type", channel_type)
         .eq("provider", provider)
         .eq("external_id", external_id)
         .limit(1).execute())
    if q.data:
        return q.data[0]
    res = client.table("channels").insert({
        "business_id": business_id,
        "channel_type": channel_type,
        "provider": provider,
        "external_id": external_id,
        "display_name": display_name,
        "status": "active",
        "config": config,
    }).execute()
    if not res.data:
        raise SystemExit(f"Failed to insert channel {channel_type}")
    return res.data[0]


def _ensure_business_member(client: Client, *, business_id: str, user_id: str, role: str = "owner") -> None:
    existing = (client.table("business_members").select("business_id")
                .eq("business_id", business_id)
                .eq("user_id", user_id)
                .limit(1).execute())
    if existing.data:
        return
    client.table("business_members").insert({
        "business_id": business_id,
        "user_id": user_id,
        "role": role,
    }).execute()


def _seed_defaults(client: Client, business_id: str) -> None:
    try:
        client.rpc("seed_revenue_edge_mvp_defaults", {"p_business_id": business_id}).execute()
    except Exception as exc:
        sys.stderr.write(f"WARN: seed defaults failed (continuing): {exc}\n")


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
    ],
    "electrical": [
        {"name": "Outlet Install", "description": "Install new electrical outlets", "base_price_low": 100, "base_price_high": 300, "required_intake_fields": ["name", "address", "quantity"], "tags": ["electrical"]},
        {"name": "Panel Upgrade", "description": "Upgrade electrical panel", "base_price_low": 1500, "base_price_high": 4000, "required_intake_fields": ["name", "address", "current_amps"], "tags": ["electrical", "upgrade"]},
    ],
    "general": [
        {"name": "Consultation", "description": "Initial consultation", "base_price_low": 0, "base_price_high": 100, "required_intake_fields": ["name", "phone"], "tags": ["general"]},
        {"name": "Standard Service", "description": "Standard service visit", "base_price_low": 100, "base_price_high": 500, "required_intake_fields": ["name", "address", "scope"], "tags": ["general"]},
    ],
}


def _seed_services(client: Client, business_id: str, vertical: str) -> int:
    presets = VERTICAL_SERVICES.get(vertical, VERTICAL_SERVICES.get("general", []))
    count = 0
    for svc in presets:
        existing = (client.table("services").select("id")
                    .eq("business_id", business_id)
                    .eq("name", svc["name"])
                    .limit(1).execute())
        if existing.data:
            continue
        client.table("services").insert({**svc, "business_id": business_id, "active": True}).execute()
        count += 1
    return count


def _seed_sample_knowledge(client: Client, business_id: str, vertical: str) -> int:
    """Seed a few knowledge items so FAQ retrieval has something to work with."""
    items = [
        {
            "title": "Business Hours",
            "content": "We are open Monday through Friday 8 AM to 6 PM, Saturday 9 AM to 2 PM, and closed on Sunday. For emergencies outside of business hours, please leave a message and we will get back to you as soon as possible.",
            "type": "faq",
        },
        {
            "title": "Service Area",
            "content": "We serve the greater metro area within a 30-mile radius. Travel fees may apply for locations outside our standard service zone.",
            "type": "policy",
        },
        {
            "title": "Pricing Policy",
            "content": "All estimates are free. Final pricing depends on the scope of work. We provide a written quote before starting any job. Payment is due upon completion. We accept cash, check, and all major credit cards.",
            "type": "policy",
        },
        {
            "title": "Cancellation Policy",
            "content": "Appointments can be cancelled or rescheduled up to 24 hours before the scheduled time at no charge. Late cancellations may incur a $50 fee.",
            "type": "policy",
        },
        {
            "title": "Emergency Services",
            "content": "We offer same-day emergency service for urgent issues. Emergency calls are prioritized and may carry an additional service fee. Call us and we will dispatch a technician as quickly as possible.",
            "type": "faq",
        },
    ]
    count = 0
    for item in items:
        existing = (client.table("knowledge_items").select("id")
                    .eq("business_id", business_id)
                    .eq("title", item["title"])
                    .limit(1).execute())
        if existing.data:
            continue
        client.table("knowledge_items").insert({
            **item,
            "business_id": business_id,
            "active": True,
            "approved": True,
            "review_required": False,
            "tags": ["auto_seeded"],
        }).execute()
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up a complete test account for E2E testing")
    parser.add_argument("--email", default="test@revenueedge.local", help="Test user email")
    parser.add_argument("--password", default="testing123!", help="Test user password")
    parser.add_argument("--business-name", default="Acme Home Services", help="Business display name")
    parser.add_argument("--slug", default="acme-home-services", help="Business URL slug")
    parser.add_argument("--vertical", default="home_services", help="Business vertical")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--did", default="+15555550123", help="Phone number (DID)")
    parser.add_argument("--services", default="plumbing", help="Vertical service presets to seed")
    args = parser.parse_args()

    print("Setting up test account...")
    print()

    # 1. Create Auth user
    print(f"1. Creating auth user: {args.email}")
    user_id = _create_auth_user(args.email, args.password)
    print(f"   user_id: {user_id}")

    # 2. Create business
    client = _client()
    print(f"2. Creating business: {args.business_name}")
    business = _upsert_business(
        client, name=args.business_name, slug=args.slug,
        vertical=args.vertical, timezone=args.timezone,
    )
    business_id = business["id"]
    print(f"   business_id: {business_id}")

    # 3. Link user to business
    print("3. Linking user to business as owner...")
    _ensure_business_member(client, business_id=business_id, user_id=user_id)
    print("   done")

    # 4. Create channels
    print(f"4. Creating channels for DID {args.did}...")
    phone_ch = _ensure_channel(client, business_id=business_id, channel_type="phone",
                               provider="retell", external_id=args.did,
                               display_name=f"Voice ({args.did})",
                               config={"from_number": args.did})
    sms_ch = _ensure_channel(client, business_id=business_id, channel_type="sms",
                             provider="retell", external_id=args.did,
                             display_name=f"SMS ({args.did})",
                             config={"from_number": args.did})
    print(f"   phone_channel_id: {phone_ch['id']}")
    print(f"   sms_channel_id: {sms_ch['id']}")

    # 5. Seed defaults
    print("5. Seeding default templates, rules, and workflows...")
    _seed_defaults(client, business_id)
    print("   done")

    # 6. Seed services
    if args.services:
        print(f"6. Seeding {args.services} service presets...")
        count = _seed_services(client, business_id, args.services)
        print(f"   {count} services created")
    else:
        print("6. Skipping service presets (use --services to seed)")

    # 7. Seed knowledge items
    print("7. Seeding sample knowledge items...")
    ki_count = _seed_sample_knowledge(client, business_id, args.vertical)
    print(f"   {ki_count} knowledge items created")

    # Print summary
    print()
    print("=" * 60)
    print("TEST ACCOUNT READY")
    print("=" * 60)
    print()
    print("Dashboard login:")
    print(f"  Email:    {args.email}")
    print(f"  Password: {args.password}")
    print(f"  URL:      http://localhost:3000/login")
    print()
    print("Business:")
    print(f"  ID:   {business_id}")
    print(f"  Name: {args.business_name}")
    print(f"  DID:  {args.did}")
    print()
    print("Quick test commands:")
    print()
    print("  # Simulate a missed call:")
    print(f"  ./scripts/simulate_webhook.sh missed-call {business_id}")
    print()
    print("  # Simulate an inbound SMS:")
    print(f'  ./scripts/simulate_webhook.sh sms {business_id} "Hi, I need a plumber"')
    print()
    print("  # Simulate an after-hours SMS:")
    print(f'  ./scripts/simulate_webhook.sh sms {business_id} "Are you open tomorrow?"')
    print()

    # Write a JSON file for other scripts to reference
    summary = {
        "email": args.email,
        "password": args.password,
        "user_id": user_id,
        "business_id": business_id,
        "business_slug": args.slug,
        "did": args.did,
        "phone_channel_id": phone_ch["id"],
        "sms_channel_id": sms_ch["id"],
    }
    with open("test_account.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("Account details saved to test_account.json")


if __name__ == "__main__":
    main()
