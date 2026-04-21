-- Optional MVP seed function for Revenue Edge Agent.
-- Run after supabase/schema.sql. Then call:
-- select public.seed_revenue_edge_mvp_defaults('YOUR_BUSINESS_UUID');

create or replace function public.seed_revenue_edge_mvp_defaults(p_business_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_business_id is null then
    raise exception 'p_business_id is required';
  end if;

  if not exists (select 1 from public.businesses where id = p_business_id) then
    raise exception 'business % does not exist', p_business_id;
  end if;

  insert into public.message_templates (business_id, name, channel_type, intent, body_template, metadata)
  select p_business_id, v.name, v.channel_type::public.channel_type, v.intent, v.body_template, v.metadata::jsonb
  from (values
    ('missed_call_recovery', 'sms', 'missed_call',
      'Thanks for calling {{business.name}} — sorry we missed you. What can we help with today?',
      '{"autopilot_safe": true}'),
    ('after_hours_intake', 'sms', 'after_hours',
      'Thanks for reaching out to {{business.name}}. We’re currently closed, but I can collect the details so the team can help you faster. What do you need help with?',
      '{"autopilot_safe": true}'),
    ('quote_intake', 'sms', 'quote_request',
      'Happy to help with a quote. What service do you need, and what’s the property address or general location?',
      '{"autopilot_safe": true}'),
    ('photo_request', 'sms', 'quote_request',
      'Photos would help us understand the scope. Could you send a few pictures of the issue or area you want us to look at?',
      '{"autopilot_safe": true}'),
    ('callback_scheduling', 'sms', 'booking_request',
      'Got it. What’s the best number and a good time for the team to call you back?',
      '{"autopilot_safe": true}'),
    ('human_handoff', 'sms', 'handoff',
      'Thanks — I’m going to have the team review this so we give you the right answer. They’ll follow up as soon as they can.',
      '{"autopilot_safe": true}'),
    ('emergency_acknowledgement', 'sms', 'urgent_service',
      'Thanks for the details. I’m alerting the team now so they can review this quickly.',
      '{"autopilot_safe": true, "do_not_promise_arrival_time": true}'),
    ('out_of_scope', 'sms', 'reject',
      'Thanks for checking. That service is outside what we currently handle, but I appreciate you reaching out.',
      '{"autopilot_safe": true}'),
    ('quote_followup_1', 'sms', 'quote_followup',
      'Just checking in — did you have any questions about the quote we sent?',
      '{"autopilot_safe": true, "attempt": 1}'),
    ('quote_followup_2', 'sms', 'quote_followup',
      'Following up again on the quote. Would you like help getting this scheduled or adjusted?',
      '{"autopilot_safe": true, "attempt": 2}'),
    ('quote_followup_final', 'sms', 'quote_followup',
      'Last follow-up from us for now. If you’d still like help, reply here and we’ll pick it back up.',
      '{"autopilot_safe": true, "attempt": 3}'),
    ('reactivation', 'sms', 'reactivation',
      'Hi {{contact.first_name | default: "there"}} — checking in from {{business.name}}. Do you still need help with {{lead.service_requested | default: "this"}}?',
      '{"autopilot_safe": true}')
  ) as v(name, channel_type, intent, body_template, metadata)
  where not exists (
    select 1 from public.message_templates mt
    where mt.business_id = p_business_id and mt.name = v.name
  );

  insert into public.business_rules (business_id, rule_type, name, priority, active, conditions, actions, notes)
  select p_business_id, v.rule_type, v.name, v.priority, true, v.conditions::jsonb, v.actions::jsonb, v.notes
  from (values
    ('handoff', 'Low confidence handoff', 10,
      '{"decision.confidence": {"lt": 0.72}}',
      '{"recommended_next_action": "handoff", "task_type": "human_handoff", "priority": 2}',
      'If confidence is low, do not freestyle. Route to a human.'),
    ('handoff', 'Complaint or sensitive issue handoff', 5,
      '{"intent": ["complaint"], "sensitive_topic_detected": true}',
      '{"recommended_next_action": "handoff", "task_type": "human_handoff", "priority": 1, "pause_autopilot": true}',
      'Complaints and sensitive topics are human-review by default.'),
    ('handoff', 'Emergency human review', 1,
      '{"urgency": ["emergency"]}',
      '{"recommended_next_action": "handoff", "task_type": "human_handoff", "priority": 1, "due_minutes": 5}',
      'Emergency language should alert a human quickly.'),
    ('autopilot', 'Allow missed-call textback', 20,
      '{"event_type": "call.missed"}',
      '{"allow": true, "template": "missed_call_recovery"}',
      'Safe first autopilot workflow.'),
    ('autopilot', 'No invented pricing', 1,
      '{"customer_asks_price": true}',
      '{"allow_price_response": false, "fallback_action": "collect_quote_details_or_handoff"}',
      'Only approved pricing rules or human-reviewed quotes can include pricing.')
  ) as v(rule_type, name, priority, conditions, actions, notes)
  where not exists (
    select 1 from public.business_rules br
    where br.business_id = p_business_id and br.name = v.name
  );

  insert into public.automation_workflows (business_id, key, name, version, status, trigger_event_type, definition)
  select p_business_id, v.key, v.name, 1, 'active'::public.workflow_status, v.trigger_event_type, v.definition::jsonb
  from (values
    ('missed_call_recovery', 'Missed-call recovery', 'call.missed',
      '{"entry_queue": "inbound-events", "next": ["outbound-actions", "follow-up-scheduler", "conversation-intelligence"]}'),
    ('after_hours_intake', 'After-hours intake', 'message.received',
      '{"entry_queue": "conversation-intelligence", "condition": "outside_business_hours", "next": ["outbound-actions", "human-handoff"]}'),
    ('quote_intake_and_draft', 'Quote intake and draft', 'quote.requested',
      '{"entry_queue": "quote-drafting", "review_default": true, "next": ["human-handoff", "follow-up-scheduler"]}'),
    ('booking_or_callback', 'Booking or callback', 'booking.requested',
      '{"entry_queue": "booking-sync", "fallback": "callback_task"}'),
    ('daily_roi_rollup', 'Daily ROI rollup', 'metric.rollup.requested',
      '{"entry_queue": "metrics-rollup"}')
  ) as v(key, name, trigger_event_type, definition)
  where not exists (
    select 1 from public.automation_workflows aw
    where aw.business_id = p_business_id and aw.key = v.key and aw.version = 1
  );
end;
$$;
