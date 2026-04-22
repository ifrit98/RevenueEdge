# Phase 5 Checklist — Reactivation + ROI

**Goal**: Prove lift. Reactivate dormant leads/customers with automated outreach, and give the business owner a clear before/after ROI view they can show their accountant.

**Depends on**: Phase 4 complete (booking + follow-up scheduler matured).

**Exit criteria**: Business owner sees a dashboard with clear numbers — missed calls recovered, response time improvement, leads captured, quotes sent, bookings made, estimated revenue attributed — and has run at least one reactivation campaign.

---

## Definition of done

- [ ] Reactivation workflow: operator selects a segment of stale leads → batch outreach → replies routed back into intelligence → outcomes tracked
- [ ] ROI dashboard page: before/after comparison of key metrics
- [ ] Operator daily summary email: end-of-day digest with actionable counts
- [ ] `metric_snapshots` includes all required KPIs
- [ ] Smoke test: `scripts/smoke_phase5.sh` seeds stale leads, triggers reactivation, asserts messages sent + metrics updated

---

## Order of operations

### 1. Stale lead reactivation workflow

#### 1a. Segment selection

- [ ] Add `lib/reactivation.py` to worker library:
  - `select_reactivation_segment(business_id, filters)`:
    - Default filters from `workflow_defaults`:
      - `leads.stage IN ('no_response', 'nurture')`
      - `leads.last_activity_at < now() - interval '30 days'` (configurable via `businesses.settings.reactivation_stale_days`, default 30)
    - Additional operator-specified filters:
      - By `leads.service_requested` (e.g., only plumbing leads)
      - By `leads.urgency` (e.g., only `routine` — don't re-poke emergencies)
      - By `leads.source` (e.g., only phone leads)
      - By `contacts.tags` (e.g., VIP customers)
    - Returns list of `{lead_id, contact_id, conversation_id, service_requested, last_activity_at}`
  - `create_reactivation_batch(business_id, segment, template_name)`:
    - For each lead in segment:
      - Check `contacts.metadata.sms_opt_out != true`
      - Enqueue a `follow-up-scheduler` job with:
        ```json
        {
          "followup_type": "reactivation",
          "lead_id": "<uuid>",
          "contact_id": "<uuid>",
          "conversation_id": "<uuid>",
          "business_id": "<uuid>",
          "template_name": "reactivation",
          "attempt": 1,
          "max_attempts": 1,
          "trace_id": "reactivation:<batch_id>"
        }
        ```
      - Stagger `available_at` across the batch (e.g., 5-second intervals) to avoid SMS burst
    - Return `{batch_id, total_leads, opted_out_skipped, enqueued}`
    - Emit `reactivation.batch_requested` event

#### 1b. Reactivation API

- [ ] `apps/api/app/routes/reactivation.py`:
  - `POST /v1/reactivation/preview` — returns segment count + sample leads matching filters (no side effects)
  - `POST /v1/reactivation/launch` — creates the batch, enqueues follow-up jobs, returns batch_id
  - `GET /v1/reactivation/:batch_id` — returns batch status (enqueued, sent, replied, converted)
  - `GET /v1/reactivation` — list recent batches for business
- [ ] Wire into `main.py`

#### 1c. Follow-up scheduler — reactivation path

- [ ] When `followup_type = 'reactivation'`:
  - Load contact, check opt-out
  - Load the `reactivation` template (from `message_templates`)
  - Enqueue `outbound-actions` with template + lead/contact context
  - Mark the batch item as `sent`
  - When customer replies: `inbound_normalizer` routes normally → `conversation-intelligence` re-classifies
  - If customer re-engages (books, requests quote, etc.) → update lead stage, credit the reactivation campaign
  - Track via events: `reactivation.sent`, `reactivation.replied`, `reactivation.converted`

### 2. Enhanced metrics rollup

- [ ] Add/verify all `metric_snapshots` columns are computed:
  - `missed_calls` — count of `inbound.call.missed` events
  - `recovered_leads` — conversations with at least one outbound message after a missed call
  - `inbound_leads` — total inbound events (calls + messages)
  - `qualified_leads` — leads that reached `qualified` stage on that date
  - `quotes_sent` — quotes with `sent_at` on that date
  - `bookings` — bookings with `scheduled_start` on that date
  - `wins` — leads that reached `won` on that date
  - `attributed_revenue` — sum of `quotes.amount_low` for won leads (simple attribution for MVP)
  - `avg_response_seconds` — median time from first inbound event to first outbound message per conversation
- [ ] `metric_snapshots.payload` (JSONB) — extended metrics:
  - `after_hours_leads`
  - `knowledge_gaps`
  - `quote_requests`
  - `quote_turnaround_seconds`
  - `callbacks_created`
  - `handoffs_created`
  - `reactivation_sent`
  - `reactivation_replies`
  - `reactivation_conversions`
  - `no_shows`
  - `total_sms_sent`
  - `total_sms_received`
- [ ] `avg_response_seconds` computation:
  - For each conversation updated on `metric_date`:
    - Find earliest inbound message `created_at`
    - Find earliest outbound message `created_at` after the inbound
    - Delta = outbound - inbound (in seconds)
  - Store the median across all conversations for the day

### 3. ROI dashboard data — before/after comparison

- [ ] `GET /v1/metrics/comparison` endpoint:
  - Accepts `baseline_start`, `baseline_end`, `comparison_start`, `comparison_end`
  - Returns for each metric:
    ```json
    {
      "metric": "missed_calls",
      "baseline_avg": 12.3,
      "comparison_avg": 4.7,
      "delta_pct": -61.8,
      "direction": "improved"
    }
    ```
  - Key comparison metrics:
    - Missed calls recovered (higher is better)
    - Avg response time (lower is better)
    - Inbound leads captured (higher is better)
    - Qualified leads (higher is better)
    - Quotes sent (higher is better)
    - Bookings (higher is better)
    - Attributed revenue (higher is better)
- [ ] The "before" period is the last N days before Revenue Edge was active (configurable; default: the 30 days before the first `events` row for this business, or manual baseline entry via `businesses.settings.baseline_metrics`)
- [ ] If no baseline exists, show absolute numbers instead of comparison

### 4. Operator daily summary email

- [ ] New service: `apps/api/app/services/daily_summary.py`:
  - `generate_daily_summary(business_id, for_date)`:
    - Load `metric_snapshots` for `for_date`
    - Load open tasks (count by type)
    - Load conversations awaiting customer (count)
    - Load conversations awaiting human (count)
    - Load knowledge gaps (count of `knowledge_review` tasks)
    - Format into the template from SKILL.md:
      ```
      Today's Revenue Edge Summary

      1. New inbound opportunities: X
      2. Missed calls recovered: X
      3. Bookings created: X
      4. Quotes needing review: X
      5. Urgent human handoffs: X
      6. Knowledge gaps to approve: X
      7. Notable conversations: ...
      ```
  - `send_daily_summary(business_id, for_date)`:
    - Load `businesses.escalation.email` (or `businesses.settings.summary_email`)
    - Call `send_email()` with the formatted summary
    - Emit `summary.sent` event
- [ ] Scheduler integration:
  - Add a second scheduled task in `apps/api/app/services/scheduler.py`:
    - Runs once per day at the business's configured "end of business" hour (default: 18:00 local)
    - For each business with `settings.daily_summary_enabled = true`:
      - Call `send_daily_summary(business_id, date.today())`
  - Alternatively, implement as a `metrics-rollup` downstream: after the EOD rollup completes, trigger the summary for businesses that have it enabled

### 5. Attributed revenue — simple model

- [ ] For MVP, attributed revenue = sum of `quotes.amount_low` for leads where `stage = 'won'` and `updated_at` falls on the metric date
- [ ] The operator can manually mark a lead as `won` via `PATCH /v1/leads/:id` with `stage = 'won'`
- [ ] Future: link to payment/invoicing systems for actual closed-won revenue

### 6. Business baseline / onboarding metrics

- [ ] `PATCH /v1/businesses/:id/settings`:
  - Operator enters baseline metrics manually:
    ```json
    {
      "baseline_metrics": {
        "avg_daily_missed_calls": 15,
        "avg_response_minutes": 240,
        "avg_daily_leads": 5,
        "period": "manual estimate before Revenue Edge"
      }
    }
    ```
  - These are used by the comparison endpoint when no actual pre-RE data exists

### 7. Smoke test

- [ ] `scripts/smoke_phase5.sh`:
  1. Seed a business with 5 leads in `stage = 'no_response'`, `last_activity_at = 45 days ago`
  2. Call `POST /v1/reactivation/preview` with default filters → assert count = 5
  3. Call `POST /v1/reactivation/launch` → assert batch created with 5 jobs
  4. Assert: 5 `reactivation.sent` events appear within 60s
  5. Simulate one reply → assert conversation re-opens + intelligence classifies
  6. Trigger a manual metrics rollup → assert `metric_snapshots.payload.reactivation_sent = 5`
  7. Call `GET /v1/metrics/comparison` with a baseline period → assert response includes delta calculations

---

## Out of scope for Phase 5

- Multi-channel reactivation (email + SMS combined campaign)
- Advanced segmentation (RFM scoring, ML-based propensity)
- A/B testing of reactivation templates
- Revenue attribution beyond simple quote-amount-for-won-leads
- Branded PDF reports for business owners
- Automated pricing optimization

---

## Risks to watch

1. **TCPA compliance at scale** — reactivation is outbound marketing, subject to TCPA. Ensure every contact was opt-in (came through an inbound channel) and hasn't STOP'd. Document the consent chain.
2. **Reactivation fatigue** — hitting the same stale leads repeatedly. Default `max_attempts = 1` for reactivation; the operator must explicitly re-launch.
3. **Baseline manipulation** — if the operator inflates manual baseline numbers, the ROI dashboard looks worse. Consider showing "improvement since Revenue Edge started" (using actual first-week data) as the primary view.
4. **Summary email deliverability** — SendGrid needs proper SPF/DKIM on the `from_email` domain. Document this in onboarding.
