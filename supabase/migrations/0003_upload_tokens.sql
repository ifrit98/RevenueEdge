-- Upload tokens for customer photo/document submission fallback.
-- Used when MMS is unavailable: send SMS with link → customer uploads → file attached to conversation.

CREATE TABLE IF NOT EXISTS public.upload_tokens (
    id          text PRIMARY KEY,
    business_id uuid NOT NULL REFERENCES public.businesses(id) ON DELETE CASCADE,
    conversation_id uuid REFERENCES public.conversations(id) ON DELETE SET NULL,
    contact_id  uuid REFERENCES public.contacts(id) ON DELETE SET NULL,
    purpose     text NOT NULL DEFAULT 'photo_request',
    expires_at  timestamptz NOT NULL,
    used        boolean NOT NULL DEFAULT false,
    metadata    jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_upload_tokens_business ON public.upload_tokens(business_id);
CREATE INDEX IF NOT EXISTS idx_upload_tokens_expires  ON public.upload_tokens(expires_at) WHERE NOT used;

ALTER TABLE public.upload_tokens ENABLE ROW LEVEL SECURITY;

CREATE POLICY upload_tokens_business_rls ON public.upload_tokens
    USING (business_id = (current_setting('request.jwt.claims', true)::jsonb ->> 'business_id')::uuid);

-- Supabase Storage bucket for customer photos (public read for simplicity).
INSERT INTO storage.buckets (id, name, public)
VALUES ('photos', 'photos', true)
ON CONFLICT (id) DO NOTHING;
