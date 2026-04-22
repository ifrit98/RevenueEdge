-- Migration: External service hardening
-- Adds p_force_dead to fail_queue_job, reaper RPC for stuck running jobs,
-- and atomic metadata merge RPC for conversations.

-- 1. Drop old 3-arg overload, then create the 4-arg version with p_force_dead
drop function if exists public.fail_queue_job(uuid, text, interval);
create or replace function public.fail_queue_job(
  p_job_id uuid,
  p_error text,
  p_retry_after interval default interval '5 minutes',
  p_force_dead boolean default false
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.queue_jobs
  set status = case
        when p_force_dead or attempts >= max_attempts then 'dead_letter'::public.queue_job_status
        else 'retry'::public.queue_job_status
      end,
      available_at = case
        when p_force_dead or attempts >= max_attempts then available_at
        else now() + coalesce(p_retry_after, interval '5 minutes')
      end,
      locked_by = null,
      locked_at = null,
      error = p_error,
      updated_at = now()
  where id = p_job_id;
end;
$$;

-- 2. Reaper RPC: reset stale running jobs back to retry
create or replace function public.reap_stale_running_jobs(
  p_stale_threshold interval default interval '10 minutes'
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  affected integer;
begin
  update public.queue_jobs
  set status = case
        when attempts >= max_attempts then 'dead_letter'::public.queue_job_status
        else 'retry'::public.queue_job_status
      end,
      available_at = now(),
      locked_by = null,
      locked_at = null,
      error = coalesce(error || '; ', '') || 'reaped: worker crash or timeout',
      updated_at = now()
  where status = 'running'
    and locked_at < now() - p_stale_threshold;

  get diagnostics affected = row_count;
  return affected;
end;
$$;

-- 3. Atomic metadata merge for conversations
create or replace function public.merge_conversation_metadata(
  p_conversation_id uuid,
  p_patch jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.conversations
  set metadata = coalesce(metadata, '{}'::jsonb) || p_patch,
      updated_at = now()
  where id = p_conversation_id;
end;
$$;
