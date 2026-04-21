-- Revenue Edge Agent Supabase/Postgres schema
-- Target: Supabase Postgres with auth.users, RLS, pgcrypto, citext, and pgvector available.
-- Run this file in the Supabase SQL editor or via a migration tool.

begin;

-- -----------------------------------------------------------------------------
-- Extensions
-- -----------------------------------------------------------------------------

create extension if not exists pgcrypto;
create extension if not exists citext;
create extension if not exists vector;
create extension if not exists pg_trgm;

-- -----------------------------------------------------------------------------
-- Enums
-- -----------------------------------------------------------------------------

do $$ begin
  create type public.business_vertical as enum (
    'appointment',
    'quote',
    'dispatch',
    'repeat_service',
    'professional_services',
    'home_services',
    'local_retail',
    'other'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.business_status as enum ('setup', 'active', 'paused', 'archived');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.business_role as enum ('owner', 'admin', 'operator', 'analyst', 'readonly');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.channel_type as enum (
    'phone',
    'sms',
    'email',
    'web_chat',
    'web_form',
    'google_business',
    'facebook',
    'instagram',
    'manual'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.channel_status as enum ('setup', 'active', 'paused', 'error', 'archived');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.message_direction as enum ('inbound', 'outbound', 'internal');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.message_sender_type as enum ('customer', 'ai', 'human', 'system', 'integration');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.conversation_status as enum (
    'open',
    'awaiting_customer',
    'awaiting_human',
    'resolved',
    'escalated'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.lead_stage as enum (
    'new',
    'contacted',
    'qualified',
    'awaiting_quote',
    'quoted',
    'booked',
    'won',
    'unqualified',
    'no_response',
    'lost',
    'nurture'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.urgency_level as enum ('emergency', 'same_day', 'soon', 'routine', 'unknown');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.value_band as enum ('unknown', 'low', 'medium', 'high', 'strategic');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.quote_status as enum (
    'drafting',
    'awaiting_review',
    'sent',
    'accepted',
    'declined',
    'expired',
    'void'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.booking_status as enum (
    'requested',
    'tentative',
    'confirmed',
    'completed',
    'cancelled',
    'no_show'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.task_status as enum ('open', 'in_progress', 'blocked', 'done', 'cancelled');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.task_type as enum (
    'human_handoff',
    'callback',
    'quote_review',
    'knowledge_review',
    'booking_review',
    'followup',
    'ops_review',
    'integration_error'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.workflow_status as enum ('draft', 'active', 'paused', 'archived');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.run_status as enum ('queued', 'running', 'succeeded', 'failed', 'cancelled');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.queue_job_status as enum ('queued', 'running', 'retry', 'succeeded', 'failed', 'dead_letter');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.knowledge_item_type as enum (
    'faq',
    'service',
    'policy',
    'pricing_rule',
    'script',
    'location_rule',
    'template',
    'other'
  );
exception when duplicate_object then null; end $$;

-- -----------------------------------------------------------------------------
-- Helper functions
-- -----------------------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create or replace function public.business_role_weight(role public.business_role)
returns integer
language sql
immutable
as $$
  select case role
    when 'owner' then 50
    when 'admin' then 40
    when 'operator' then 30
    when 'analyst' then 20
    when 'readonly' then 10
    else 0
  end;
$$;

-- -----------------------------------------------------------------------------
-- Core tenant tables
-- -----------------------------------------------------------------------------

create table if not exists public.businesses (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug citext unique,
  vertical public.business_vertical not null default 'other',
  timezone text not null default 'America/New_York',
  status public.business_status not null default 'setup',
  service_area jsonb not null default '{}'::jsonb,
  hours jsonb not null default '{}'::jsonb,
  escalation jsonb not null default '{}'::jsonb,
  settings jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (name <> '')
);

create table if not exists public.business_members (
  business_id uuid not null references public.businesses(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role public.business_role not null default 'operator',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (business_id, user_id)
);

-- These SECURITY DEFINER helpers intentionally bypass RLS to evaluate membership.
create or replace function public.is_business_member(target_business_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.business_members bm
    where bm.business_id = target_business_id
      and bm.user_id = auth.uid()
  );
$$;

create or replace function public.has_business_role(
  target_business_id uuid,
  minimum_role public.business_role
)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.business_members bm
    where bm.business_id = target_business_id
      and bm.user_id = auth.uid()
      and public.business_role_weight(bm.role) >= public.business_role_weight(minimum_role)
  );
$$;

create or replace function public.create_business_with_owner(
  p_name text,
  p_slug citext default null,
  p_vertical public.business_vertical default 'other',
  p_timezone text default 'America/New_York'
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  new_business_id uuid;
begin
  if auth.uid() is null then
    raise exception 'Must be authenticated to create a business';
  end if;

  insert into public.businesses (name, slug, vertical, timezone)
  values (p_name, p_slug, p_vertical, p_timezone)
  returning id into new_business_id;

  insert into public.business_members (business_id, user_id, role)
  values (new_business_id, auth.uid(), 'owner');

  return new_business_id;
end;
$$;

-- -----------------------------------------------------------------------------
-- Business configuration
-- -----------------------------------------------------------------------------

create table if not exists public.services (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  name text not null,
  description text,
  active boolean not null default true,
  base_price_low numeric(12,2),
  base_price_high numeric(12,2),
  currency text not null default 'USD',
  price_policy jsonb not null default '{}'::jsonb,
  required_intake_fields text[] not null default '{}'::text[],
  tags text[] not null default '{}'::text[],
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (name <> ''),
  check (base_price_low is null or base_price_low >= 0),
  check (base_price_high is null or base_price_high >= 0),
  check (base_price_low is null or base_price_high is null or base_price_high >= base_price_low)
);

create table if not exists public.business_rules (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  rule_type text not null,
  name text not null,
  priority integer not null default 100,
  active boolean not null default true,
  conditions jsonb not null default '{}'::jsonb,
  actions jsonb not null default '{}'::jsonb,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (rule_type <> ''),
  check (name <> '')
);

create table if not exists public.channels (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  channel_type public.channel_type not null,
  provider text not null,
  external_id text,
  display_name text,
  status public.channel_status not null default 'setup',
  config jsonb not null default '{}'::jsonb,
  last_sync_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (provider <> '')
);

create unique index if not exists channels_provider_external_uidx
  on public.channels (business_id, channel_type, provider, external_id)
  where external_id is not null;

create table if not exists public.message_templates (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  name text not null,
  channel_type public.channel_type,
  intent text,
  body_template text not null,
  active boolean not null default true,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (name <> ''),
  check (body_template <> '')
);

-- -----------------------------------------------------------------------------
-- Customer and conversation data
-- -----------------------------------------------------------------------------

create table if not exists public.contacts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  name text,
  phone_e164 text,
  email citext,
  address jsonb not null default '{}'::jsonb,
  source_channel public.channel_type,
  tags text[] not null default '{}'::text[],
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (phone_e164 is null or phone_e164 ~ '^\+[1-9][0-9]{6,14}$')
);

create unique index if not exists contacts_business_phone_uidx
  on public.contacts (business_id, phone_e164)
  where phone_e164 is not null;

create unique index if not exists contacts_business_email_uidx
  on public.contacts (business_id, email)
  where email is not null;

create index if not exists contacts_business_tags_idx on public.contacts using gin (tags);

create table if not exists public.conversations (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  contact_id uuid references public.contacts(id) on delete set null,
  channel_id uuid references public.channels(id) on delete set null,
  channel_type public.channel_type not null,
  status public.conversation_status not null default 'open',
  current_intent text,
  urgency public.urgency_level not null default 'unknown',
  ai_confidence numeric(4,3),
  summary text,
  assigned_to uuid references auth.users(id) on delete set null,
  last_message_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (ai_confidence is null or (ai_confidence >= 0 and ai_confidence <= 1))
);

create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  contact_id uuid references public.contacts(id) on delete set null,
  channel_id uuid references public.channels(id) on delete set null,
  direction public.message_direction not null,
  sender_type public.message_sender_type not null,
  sender_user_id uuid references auth.users(id) on delete set null,
  body text,
  normalized_body text,
  attachments jsonb not null default '[]'::jsonb,
  external_message_id text,
  idempotency_key text,
  model_name text,
  token_usage jsonb not null default '{}'::jsonb,
  raw_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  check (jsonb_typeof(attachments) = 'array')
);

create unique index if not exists messages_business_external_uidx
  on public.messages (business_id, external_message_id)
  where external_message_id is not null;

create unique index if not exists messages_idempotency_uidx
  on public.messages (idempotency_key)
  where idempotency_key is not null;

-- Keep conversation freshness current when messages arrive.
create or replace function public.touch_conversation_from_message()
returns trigger
language plpgsql
as $$
begin
  update public.conversations
  set last_message_at = greatest(coalesce(last_message_at, new.created_at), new.created_at),
      updated_at = now()
  where id = new.conversation_id;
  return new;
end;
$$;

-- -----------------------------------------------------------------------------
-- Revenue objects
-- -----------------------------------------------------------------------------

create table if not exists public.leads (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  contact_id uuid references public.contacts(id) on delete set null,
  conversation_id uuid references public.conversations(id) on delete set null,
  service_id uuid references public.services(id) on delete set null,
  service_requested text,
  urgency public.urgency_level not null default 'unknown',
  value_band public.value_band not null default 'unknown',
  fit_score numeric(4,3),
  stage public.lead_stage not null default 'new',
  source text,
  estimated_value_low numeric(12,2),
  estimated_value_high numeric(12,2),
  currency text not null default 'USD',
  lost_reason text,
  owner_user_id uuid references auth.users(id) on delete set null,
  last_activity_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (fit_score is null or (fit_score >= 0 and fit_score <= 1)),
  check (estimated_value_low is null or estimated_value_low >= 0),
  check (estimated_value_high is null or estimated_value_high >= 0),
  check (estimated_value_low is null or estimated_value_high is null or estimated_value_high >= estimated_value_low)
);

create table if not exists public.intake_fields (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  lead_id uuid not null references public.leads(id) on delete cascade,
  field_name text not null,
  field_value jsonb not null,
  confidence numeric(4,3),
  source_message_id uuid references public.messages(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (field_name <> ''),
  check (confidence is null or (confidence >= 0 and confidence <= 1)),
  unique (lead_id, field_name)
);

create table if not exists public.quotes (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  lead_id uuid not null references public.leads(id) on delete cascade,
  status public.quote_status not null default 'drafting',
  quote_type text not null default 'estimate',
  amount_low numeric(12,2),
  amount_high numeric(12,2),
  currency text not null default 'USD',
  draft_text text,
  terms text,
  approved_by uuid references auth.users(id) on delete set null,
  sent_at timestamptz,
  expires_at timestamptz,
  accepted_at timestamptz,
  declined_at timestamptz,
  external_quote_id text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (amount_low is null or amount_low >= 0),
  check (amount_high is null or amount_high >= 0),
  check (amount_low is null or amount_high is null or amount_high >= amount_low)
);

create table if not exists public.bookings (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  lead_id uuid references public.leads(id) on delete set null,
  contact_id uuid references public.contacts(id) on delete set null,
  status public.booking_status not null default 'requested',
  scheduled_start timestamptz,
  scheduled_end timestamptz,
  timezone text not null default 'America/New_York',
  assignee_user_id uuid references auth.users(id) on delete set null,
  external_calendar_event_id text,
  location jsonb not null default '{}'::jsonb,
  notes text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (scheduled_start is null or scheduled_end is null or scheduled_end > scheduled_start)
);

create table if not exists public.tasks (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  type public.task_type not null,
  title text not null,
  description text,
  status public.task_status not null default 'open',
  priority smallint not null default 3,
  assigned_to uuid references auth.users(id) on delete set null,
  due_at timestamptz,
  source_table text,
  source_id uuid,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (title <> ''),
  check (priority between 1 and 5)
);

-- -----------------------------------------------------------------------------
-- Knowledge base
-- -----------------------------------------------------------------------------

create table if not exists public.knowledge_sources (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  source_type text not null,
  uri text,
  title text,
  ingestion_status text not null default 'pending',
  last_ingested_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (source_type <> '')
);

create table if not exists public.knowledge_items (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  source_id uuid references public.knowledge_sources(id) on delete set null,
  type public.knowledge_item_type not null default 'other',
  title text not null,
  content text not null,
  tags text[] not null default '{}'::text[],
  active boolean not null default true,
  approved boolean not null default false,
  review_required boolean not null default true,
  last_reviewed_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  embedding vector(1536),
  search_tsv tsvector generated always as (
    to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, ''))
  ) stored,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (title <> ''),
  check (content <> '')
);

create index if not exists knowledge_items_search_idx on public.knowledge_items using gin (search_tsv);
create index if not exists knowledge_items_tags_idx on public.knowledge_items using gin (tags);
create index if not exists knowledge_items_embedding_hnsw_idx
  on public.knowledge_items using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);

-- -----------------------------------------------------------------------------
-- Automation, events, and queueing
-- -----------------------------------------------------------------------------

create table if not exists public.events (
  id uuid primary key default gen_random_uuid(),
  business_id uuid references public.businesses(id) on delete cascade,
  event_type text not null,
  aggregate_type text,
  aggregate_id uuid,
  payload jsonb not null default '{}'::jsonb,
  idempotency_key text,
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  check (event_type <> '')
);

create unique index if not exists events_idempotency_uidx
  on public.events (idempotency_key)
  where idempotency_key is not null;

create table if not exists public.automation_workflows (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  key text not null,
  name text not null,
  version integer not null default 1,
  status public.workflow_status not null default 'draft',
  trigger_event_type text not null,
  definition jsonb not null default '{}'::jsonb,
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (key <> ''),
  check (name <> ''),
  check (trigger_event_type <> ''),
  unique (business_id, key, version)
);

create table if not exists public.automation_runs (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  workflow_id uuid references public.automation_workflows(id) on delete set null,
  conversation_id uuid references public.conversations(id) on delete set null,
  lead_id uuid references public.leads(id) on delete set null,
  status public.run_status not null default 'queued',
  input_payload jsonb not null default '{}'::jsonb,
  output_payload jsonb not null default '{}'::jsonb,
  error text,
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.action_runs (
  id uuid primary key default gen_random_uuid(),
  business_id uuid references public.businesses(id) on delete cascade,
  action_type text not null,
  status public.run_status not null default 'queued',
  idempotency_key text,
  payload jsonb not null default '{}'::jsonb,
  result jsonb not null default '{}'::jsonb,
  error text,
  attempts integer not null default 0,
  run_after timestamptz not null default now(),
  locked_by text,
  locked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (action_type <> ''),
  check (attempts >= 0)
);

create unique index if not exists action_runs_idempotency_uidx
  on public.action_runs (idempotency_key)
  where idempotency_key is not null;

create table if not exists public.queue_jobs (
  id uuid primary key default gen_random_uuid(),
  business_id uuid references public.businesses(id) on delete cascade,
  queue_name text not null,
  status public.queue_job_status not null default 'queued',
  priority integer not null default 100,
  payload jsonb not null default '{}'::jsonb,
  result jsonb not null default '{}'::jsonb,
  idempotency_key text,
  available_at timestamptz not null default now(),
  attempts integer not null default 0,
  max_attempts integer not null default 5,
  locked_by text,
  locked_at timestamptz,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (queue_name <> ''),
  check (attempts >= 0),
  check (max_attempts > 0)
);

create unique index if not exists queue_jobs_idempotency_uidx
  on public.queue_jobs (idempotency_key)
  where idempotency_key is not null;

create index if not exists queue_jobs_claim_idx
  on public.queue_jobs (queue_name, status, available_at, priority, created_at)
  where status in ('queued', 'retry');

create or replace function public.enqueue_event(
  p_event_type text,
  p_payload jsonb,
  p_business_id uuid default null,
  p_aggregate_type text default null,
  p_aggregate_id uuid default null,
  p_idempotency_key text default null,
  p_occurred_at timestamptz default now()
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  new_event_id uuid;
begin
  insert into public.events (
    business_id,
    event_type,
    aggregate_type,
    aggregate_id,
    payload,
    idempotency_key,
    occurred_at
  ) values (
    p_business_id,
    p_event_type,
    p_aggregate_type,
    p_aggregate_id,
    coalesce(p_payload, '{}'::jsonb),
    p_idempotency_key,
    p_occurred_at
  )
  on conflict (idempotency_key) where idempotency_key is not null
  do update set idempotency_key = excluded.idempotency_key
  returning id into new_event_id;

  return new_event_id;
end;
$$;

create or replace function public.enqueue_job(
  p_queue_name text,
  p_payload jsonb,
  p_business_id uuid default null,
  p_available_at timestamptz default now(),
  p_priority integer default 100,
  p_idempotency_key text default null,
  p_max_attempts integer default 5
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  new_job_id uuid;
begin
  insert into public.queue_jobs (
    business_id,
    queue_name,
    payload,
    available_at,
    priority,
    idempotency_key,
    max_attempts
  ) values (
    p_business_id,
    p_queue_name,
    coalesce(p_payload, '{}'::jsonb),
    coalesce(p_available_at, now()),
    coalesce(p_priority, 100),
    p_idempotency_key,
    coalesce(p_max_attempts, 5)
  )
  on conflict (idempotency_key) where idempotency_key is not null
  do update set idempotency_key = excluded.idempotency_key
  returning id into new_job_id;

  return new_job_id;
end;
$$;

create or replace function public.claim_queue_jobs(
  p_queue_name text,
  p_worker_id text,
  p_limit integer default 10,
  p_lock_timeout interval default interval '5 minutes'
)
returns setof public.queue_jobs
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  with candidates as (
    select q.id
    from public.queue_jobs q
    where q.queue_name = p_queue_name
      and q.status in ('queued', 'retry')
      and q.available_at <= now()
      and (q.locked_at is null or q.locked_at < now() - p_lock_timeout)
    order by q.priority asc, q.available_at asc, q.created_at asc
    limit greatest(1, least(coalesce(p_limit, 10), 100))
    for update skip locked
  )
  update public.queue_jobs q
  set status = 'running',
      locked_by = p_worker_id,
      locked_at = now(),
      attempts = q.attempts + 1,
      updated_at = now()
  from candidates c
  where q.id = c.id
  returning q.*;
end;
$$;

create or replace function public.complete_queue_job(
  p_job_id uuid,
  p_result jsonb default '{}'::jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.queue_jobs
  set status = 'succeeded',
      result = coalesce(p_result, '{}'::jsonb),
      locked_by = null,
      locked_at = null,
      error = null,
      updated_at = now()
  where id = p_job_id;
end;
$$;

create or replace function public.fail_queue_job(
  p_job_id uuid,
  p_error text,
  p_retry_after interval default interval '5 minutes'
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.queue_jobs
  set status = case
        when attempts >= max_attempts then 'dead_letter'::public.queue_job_status
        else 'retry'::public.queue_job_status
      end,
      available_at = case
        when attempts >= max_attempts then available_at
        else now() + coalesce(p_retry_after, interval '5 minutes')
      end,
      locked_by = null,
      locked_at = null,
      error = p_error,
      updated_at = now()
  where id = p_job_id;
end;
$$;

-- -----------------------------------------------------------------------------
-- Analytics and audit
-- -----------------------------------------------------------------------------

create table if not exists public.metric_snapshots (
  business_id uuid not null references public.businesses(id) on delete cascade,
  metric_date date not null,
  missed_calls integer not null default 0,
  recovered_leads integer not null default 0,
  inbound_leads integer not null default 0,
  qualified_leads integer not null default 0,
  quotes_sent integer not null default 0,
  bookings integer not null default 0,
  wins integer not null default 0,
  attributed_revenue numeric(14,2) not null default 0,
  avg_response_seconds numeric(12,2),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (business_id, metric_date),
  check (missed_calls >= 0),
  check (recovered_leads >= 0),
  check (inbound_leads >= 0),
  check (qualified_leads >= 0),
  check (quotes_sent >= 0),
  check (bookings >= 0),
  check (wins >= 0),
  check (attributed_revenue >= 0)
);

create table if not exists public.audit_log (
  id uuid primary key default gen_random_uuid(),
  business_id uuid references public.businesses(id) on delete cascade,
  actor_user_id uuid references auth.users(id) on delete set null,
  action text not null,
  target_table text,
  target_id uuid,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  check (action <> '')
);

-- -----------------------------------------------------------------------------
-- Indexes
-- -----------------------------------------------------------------------------

create index if not exists businesses_status_idx on public.businesses (status);
create index if not exists services_business_active_idx on public.services (business_id, active);
create index if not exists business_rules_business_type_idx on public.business_rules (business_id, rule_type, active, priority);
create index if not exists channels_business_status_idx on public.channels (business_id, status);
create index if not exists conversations_business_status_idx on public.conversations (business_id, status, last_message_at desc);
create index if not exists conversations_contact_idx on public.conversations (contact_id, created_at desc);
create index if not exists messages_conversation_created_idx on public.messages (conversation_id, created_at asc);
create index if not exists messages_business_created_idx on public.messages (business_id, created_at desc);
create index if not exists leads_business_stage_idx on public.leads (business_id, stage, updated_at desc);
create index if not exists leads_contact_idx on public.leads (contact_id, created_at desc);
create index if not exists intake_fields_lead_idx on public.intake_fields (lead_id);
create index if not exists quotes_business_status_idx on public.quotes (business_id, status, updated_at desc);
create index if not exists bookings_business_time_idx on public.bookings (business_id, scheduled_start, status);
create index if not exists tasks_business_status_due_idx on public.tasks (business_id, status, due_at asc nulls last, priority asc);
create index if not exists events_business_type_time_idx on public.events (business_id, event_type, occurred_at desc);
create index if not exists automation_runs_business_status_idx on public.automation_runs (business_id, status, created_at desc);
create index if not exists action_runs_business_status_idx on public.action_runs (business_id, status, run_after);
create index if not exists metric_snapshots_business_date_idx on public.metric_snapshots (business_id, metric_date desc);

-- -----------------------------------------------------------------------------
-- Triggers
-- -----------------------------------------------------------------------------

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'businesses',
    'business_members',
    'services',
    'business_rules',
    'channels',
    'message_templates',
    'contacts',
    'conversations',
    'leads',
    'intake_fields',
    'quotes',
    'bookings',
    'tasks',
    'knowledge_sources',
    'knowledge_items',
    'automation_workflows',
    'automation_runs',
    'action_runs',
    'queue_jobs',
    'metric_snapshots'
  ] loop
    execute format('drop trigger if exists set_updated_at on public.%I', table_name);
    execute format('create trigger set_updated_at before update on public.%I for each row execute function public.set_updated_at()', table_name);
  end loop;
end $$;

drop trigger if exists touch_conversation_after_message on public.messages;
create trigger touch_conversation_after_message
  after insert on public.messages
  for each row execute function public.touch_conversation_from_message();

-- -----------------------------------------------------------------------------
-- Row Level Security
-- -----------------------------------------------------------------------------

alter table public.businesses enable row level security;
alter table public.business_members enable row level security;

drop policy if exists businesses_select on public.businesses;
create policy businesses_select
  on public.businesses for select
  using (public.is_business_member(id));

drop policy if exists businesses_update_admin on public.businesses;
create policy businesses_update_admin
  on public.businesses for update
  using (public.has_business_role(id, 'admin'))
  with check (public.has_business_role(id, 'admin'));

drop policy if exists businesses_delete_owner on public.businesses;
create policy businesses_delete_owner
  on public.businesses for delete
  using (public.has_business_role(id, 'owner'));

-- Direct business inserts are intentionally omitted. Use create_business_with_owner().

drop policy if exists business_members_select on public.business_members;
create policy business_members_select
  on public.business_members for select
  using (public.is_business_member(business_id));

drop policy if exists business_members_insert_owner on public.business_members;
create policy business_members_insert_owner
  on public.business_members for insert
  with check (public.has_business_role(business_id, 'owner'));

drop policy if exists business_members_update_owner on public.business_members;
create policy business_members_update_owner
  on public.business_members for update
  using (public.has_business_role(business_id, 'owner'))
  with check (public.has_business_role(business_id, 'owner'));

drop policy if exists business_members_delete_owner on public.business_members;
create policy business_members_delete_owner
  on public.business_members for delete
  using (public.has_business_role(business_id, 'owner'));

-- Generic tenant policies for all tables with business_id.
do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'services',
    'business_rules',
    'channels',
    'message_templates',
    'contacts',
    'conversations',
    'messages',
    'leads',
    'intake_fields',
    'quotes',
    'bookings',
    'tasks',
    'knowledge_sources',
    'knowledge_items',
    'events',
    'automation_workflows',
    'automation_runs',
    'action_runs',
    'queue_jobs',
    'metric_snapshots',
    'audit_log'
  ] loop
    execute format('alter table public.%I enable row level security', table_name);

    execute format('drop policy if exists tenant_select on public.%I', table_name);
    execute format(
      'create policy tenant_select on public.%I for select using (business_id is not null and public.is_business_member(business_id))',
      table_name
    );

    execute format('drop policy if exists tenant_insert_member on public.%I', table_name);
    execute format(
      'create policy tenant_insert_member on public.%I for insert with check (business_id is not null and public.is_business_member(business_id))',
      table_name
    );

    execute format('drop policy if exists tenant_update_member on public.%I', table_name);
    execute format(
      'create policy tenant_update_member on public.%I for update using (business_id is not null and public.is_business_member(business_id)) with check (business_id is not null and public.is_business_member(business_id))',
      table_name
    );

    execute format('drop policy if exists tenant_delete_admin on public.%I', table_name);
    execute format(
      'create policy tenant_delete_admin on public.%I for delete using (business_id is not null and public.has_business_role(business_id, ''admin''))',
      table_name
    );
  end loop;
end $$;


-- -----------------------------------------------------------------------------
-- Function grants
-- -----------------------------------------------------------------------------

-- Business onboarding can be called by authenticated users.
revoke execute on function public.create_business_with_owner(text, citext, public.business_vertical, text) from public;
grant execute on function public.create_business_with_owner(text, citext, public.business_vertical, text) to authenticated;

-- Queue/event helper functions are intended for trusted server-side workers.
-- Supabase's service_role bypasses RLS and should be used from secure backend code only.
revoke execute on function public.enqueue_event(text, jsonb, uuid, text, uuid, text, timestamptz) from public;
revoke execute on function public.enqueue_job(text, jsonb, uuid, timestamptz, integer, text, integer) from public;
revoke execute on function public.claim_queue_jobs(text, text, integer, interval) from public;
revoke execute on function public.complete_queue_job(uuid, jsonb) from public;
revoke execute on function public.fail_queue_job(uuid, text, interval) from public;

grant execute on function public.enqueue_event(text, jsonb, uuid, text, uuid, text, timestamptz) to service_role;
grant execute on function public.enqueue_job(text, jsonb, uuid, timestamptz, integer, text, integer) to service_role;
grant execute on function public.claim_queue_jobs(text, text, integer, interval) to service_role;
grant execute on function public.complete_queue_job(uuid, jsonb) to service_role;
grant execute on function public.fail_queue_job(uuid, text, interval) to service_role;

-- -----------------------------------------------------------------------------
-- Seedable global defaults are deliberately omitted from this schema file.
-- Put business-specific workflow definitions in automation_workflows.definition.
-- Put machine-readable queue workflows in workflows/queue_workflow_pack.yaml.
-- -----------------------------------------------------------------------------

commit;
