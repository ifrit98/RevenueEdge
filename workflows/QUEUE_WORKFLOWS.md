# Revenue Edge Agent Queue Workflow Pack

This runbook explains how to operate the queue/workflow layer defined in `queue_workflow_pack.yaml` and backed by `public.queue_jobs` in `supabase/schema.sql`.

## Mental model

The system is event-first and queue-driven.

```text
Provider webhook -> inbound event -> normalized records -> decision object -> action queue -> customer response / task / quote / booking -> metrics
```

Do not let channel adapters perform business logic. Channel adapters should normalize provider payloads, persist an event, and enqueue work. Business logic belongs in the intelligence, policy, and action workers.

---

## Queue lifecycle

A queue job moves through these states:

```text
queued -> running -> succeeded
queued -> running -> retry -> running -> succeeded
queued -> running -> retry -> running -> dead_letter
```

Workers should claim jobs using:

```sql
select * from public.claim_queue_jobs('conversation-intelligence', 'worker-01', 10);
```

After successful processing:

```sql
select public.complete_queue_job(:job_id, '{"ok": true}'::jsonb);
```

After retryable failure:

```sql
select public.fail_queue_job(:job_id, 'temporary_provider_error', interval '5 minutes');
```

The schema handles retries and moves jobs to `dead_letter` after `max_attempts`.

---

## Required worker pattern

Every worker should follow this pattern:

1. Claim jobs with `claim_queue_jobs`.
2. Validate `payload` against the queue contract.
3. Load current business configuration.
4. Verify idempotency.
5. Perform one bounded unit of work.
6. Persist database changes in a transaction.
7. Emit an event to `public.events`.
8. Enqueue the next job, if needed.
9. Complete or fail the original job.

Pseudo-code:

```ts
for (const job of await claimJobs(queueName, workerId, limit)) {
  try {
    await db.transaction(async (tx) => {
      const context = await loadContext(tx, job.payload.business_id, job.payload);
      await ensureIdempotent(tx, job.idempotency_key);
      const result = await handleJob(tx, context, job.payload);
      await emitEvents(tx, result.events);
      await enqueueNextJobs(tx, result.jobs);
      await completeJob(tx, job.id, result.summary);
    });
  } catch (err) {
    await failJob(job.id, classifyError(err), retryDelay(err));
  }
}
```

---

## Event contracts

### `call.missed`

Created by a phone adapter when a call is not answered.

Required payload:

```json
{
  "provider": "twilio",
  "provider_call_id": "CA...",
  "from_phone_e164": "+15555550123",
  "to_phone_e164": "+15555550999",
  "occurred_at": "2026-04-21T14:30:00Z"
}
```

Next queue: `inbound-events`.

### `message.received`

Created by SMS, email, web chat, web form, or social adapter.

Required payload:

```json
{
  "provider": "twilio",
  "external_message_id": "SM...",
  "channel_type": "sms",
  "from": "+15555550123",
  "to": "+15555550999",
  "body": "Hi, I need a quote for landscaping",
  "attachments": [],
  "occurred_at": "2026-04-21T14:31:00Z"
}
```

Next queue: `inbound-events`.

### `conversation.classified`

Created by the intelligence worker after it outputs a decision object.

Required payload:

```json
{
  "conversation_id": "uuid",
  "decision": {
    "intent": "quote_request",
    "urgency": "routine",
    "confidence": 0.91,
    "recommended_next_action": "ask_question",
    "missing_fields": ["property_address_or_location"],
    "customer_response": "Happy to help with a quote. What is the property address or general location?"
  }
}
```

Next queue depends on `recommended_next_action`.

---

## Workflow 1: missed-call recovery

Goal: turn a missed call into a recoverable conversation in seconds.

### Trigger

`call.missed`

### Steps

1. `inbound_normalizer` upserts the contact by phone number.
2. `inbound_normalizer` creates or reopens a conversation.
3. `outbound_action_worker` sends the missed-call text-back.
4. `followup_scheduler_worker` schedules a no-reply follow-up.
5. When the customer replies, `conversation_intelligence_worker` classifies the reply and routes it.

### Default message

```text
Thanks for calling {{business.name}} — sorry we missed you. What can we help with today?
```

### Database writes

- `contacts`
- `conversations`
- `messages`
- `events`
- `queue_jobs`
- optional `leads`

### Success metrics

- missed calls
- recovered leads
- average first-response seconds
- lead-to-booking rate from missed calls

---

## Workflow 2: after-hours intake

Goal: keep the business effectively open without promising unsupported service.

### Trigger

`message.received` or `web_form.submitted` outside configured business hours.

### Steps

1. Classify intent and urgency.
2. If emergency, route to `emergency_triage`.
3. If normal, answer from approved knowledge and collect relevant intake fields.
4. Create a lead or task.
5. Schedule a human review for next business opening if required.

### Default message

```text
Thanks for reaching out to {{business.name}}. We’re currently closed, but I can collect the details so the team can help you faster. What do you need help with?
```

### Success metrics

- after-hours leads
- qualified after-hours leads
- after-hours bookings
- after-hours handoffs

---

## Workflow 3: quote intake and draft

Goal: compress the time from inquiry to estimate without letting the model invent pricing.

### Trigger

`quote.requested` or an inbound message classified as `quote_request`.

### Required default fields

- name
- phone or email
- service requested
- property address or general location
- scope or issue
- urgency
- photos, when useful

### Steps

1. Match the requested service to `services`.
2. Check service area and service availability.
3. Ask for one or two missing fields at a time.
4. Save fields to `intake_fields`.
5. Create or update `leads`.
6. Draft a quote when enough information exists.
7. Set quote to `awaiting_review` unless an approved auto-send rule exists.
8. Schedule quote recovery after sending.

### Hard blocks

- no service match
- no approved pricing rule
- required fields missing
- high-value unusual scope
- low confidence
- sensitive issue

### Success metrics

- quote requests
- quote drafts
- quotes sent
- estimate turnaround seconds
- quote-to-win conversion

---

## Workflow 4: booking or callback

Goal: get qualified prospects onto the calendar or into a callback task.

### Trigger

`booking.requested` or inbound message classified as `booking_request`.

### Booking allowed only if

- booking automation is enabled
- service is in scope
- required fields are complete
- real availability exists
- customer selected or accepted a slot
- slot obeys business rules

### Steps

1. Verify booking policy.
2. Check availability.
3. Offer slots or confirm a selected slot.
4. Create booking.
5. Sync to calendar/CRM/job system.
6. Send confirmation.
7. If blocked, create callback task.

### Success metrics

- booking requests
- bookings created
- callbacks created
- booking conversion rate

---

## Workflow 5: emergency triage

Goal: collect essential details and alert a human quickly.

### Trigger

Urgency classified as `emergency`, or a business rule identifies emergency language.

### Required fields

- name
- phone
- location
- issue
- immediate safety or access constraints

### Rules

- Do not promise an arrival time unless the business has an explicit approved rule.
- Do not continue normal AI back-and-forth if an urgent human handoff is required.
- Create priority `1` task and notify the escalation contact.

### Success metrics

- emergency handoffs
- emergency acknowledgement time
- human response time

---

## Workflow 6: complaint or sensitive handoff

Goal: prevent autopilot from mishandling sensitive interactions.

### Trigger

Intent classified as `complaint`, or sensitive topic detected.

Sensitive topics include:

- angry customer
- refund demand
- threat of legal action
- insurance issue
- injury or safety issue
- payment dispute
- regulatory issue
- public review threat

### Steps

1. Summarize issue internally.
2. Send neutral acknowledgement.
3. Create priority handoff task.
4. Pause autopilot for that conversation.

### Default message

```text
Thanks for letting us know. I’m going to have the team review this so we can respond properly.
```

---

## Workflow 7: quote recovery follow-up

Goal: recover revenue from quotes that were sent but not answered.

### Trigger

`quote.sent` schedules future `followup.due` events.

### Default cadence

- attempt 1: 1 day after quote sent
- attempt 2: 3 days after quote sent
- attempt 3: 7 days after quote sent

### Stop conditions

- customer replied
- lead is booked
- lead is won
- lead is lost
- human override
- max attempts reached

### Success metrics

- quote follow-ups sent
- quote replies recovered
- quote recovery wins

---

## Workflow 8: stale lead reactivation

Goal: turn old leads or dormant customers into new conversations.

### Trigger

Manual or scheduled `reactivation.batch_requested`.

### Segment examples

- leads in `no_response` for more than 30 days
- leads in `nurture` for more than 60 days
- past customers due for seasonal service
- customers who asked for a quote but never booked

### Rules

- Respect opt-outs.
- Use approved templates.
- Stop on reply.
- Route replies to `conversation-intelligence`.

---

## Handoff task format

Every handoff task should include:

```json
{
  "title": "Urgent lead needs review",
  "type": "human_handoff",
  "priority": 1,
  "description": "Customer says water is actively leaking under the sink. Needs same-day review.",
  "metadata": {
    "conversation_id": "uuid",
    "contact_id": "uuid",
    "lead_id": "uuid",
    "intent": "urgent_service",
    "urgency": "emergency",
    "missing_fields": ["address"],
    "recommended_next_step": "Call customer and confirm dispatch availability"
  }
}
```

---

## Minimal worker set for MVP

You can launch the first version with five workers:

1. `inbound_normalizer`
2. `conversation_intelligence_worker`
3. `outbound_action_worker`
4. `handoff_worker`
5. `followup_scheduler_worker`

Add `quote_drafting_worker`, `booking_worker`, `knowledge_ingestion_worker`, and `metrics_rollup_worker` after the first channel produces value.

---

## Minimum launch configuration

For one SMB pilot, configure:

- one `businesses` row
- one owner in `business_members`
- one SMS/phone channel in `channels`
- at least five `knowledge_items`
- at least one `service`
- at least one missed-call workflow rule
- one escalation contact in `businesses.escalation`
- one operator inbox view over `tasks`

Launch in shadow mode first, then enable missed-call text-back.
