"use client";

import { createClient } from "@/lib/supabase/client";
import { useEffect, useState } from "react";

type Business = {
  id: string;
  name: string;
  industry: string | null;
  timezone: string | null;
  settings: Record<string, unknown>;
};

export default function BusinessSettingsPage() {
  const [biz, setBiz] = useState<Business | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ name: "", industry: "", timezone: "" });
  const supabase = createClient();

  useEffect(() => {
    supabase
      .from("businesses")
      .select("id, name, industry, timezone, settings")
      .limit(1)
      .single()
      .then(({ data }) => {
        const b = data as Business | null;
        setBiz(b);
        if (b) setForm({ name: b.name, industry: b.industry || "", timezone: b.timezone || "" });
        setLoading(false);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (!biz) return;
    setSaving(true);
    await supabase
      .from("businesses")
      .update({ name: form.name, industry: form.industry || null, timezone: form.timezone || null })
      .eq("id", biz.id);
    setSaving(false);
  };

  if (loading) return <div className="text-sm text-muted-foreground">Loading...</div>;
  if (!biz) return <div className="text-sm text-muted-foreground">No business found.</div>;

  return (
    <div className="max-w-lg">
      <h1 className="text-2xl font-semibold text-foreground">Business Settings</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Manage your business profile and preferences.
      </p>

      <div className="mt-6 space-y-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Business Name</label>
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full rounded-md border border-border px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Industry</label>
          <input
            value={form.industry}
            onChange={(e) => setForm({ ...form, industry: e.target.value })}
            className="w-full rounded-md border border-border px-3 py-2 text-sm"
            placeholder="e.g. plumbing, landscaping, dental"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Timezone</label>
          <input
            value={form.timezone}
            onChange={(e) => setForm({ ...form, timezone: e.target.value })}
            className="w-full rounded-md border border-border px-3 py-2 text-sm"
            placeholder="e.g. America/New_York"
          />
        </div>
        <button
          onClick={save}
          disabled={saving}
          className="rounded-lg bg-primary px-6 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save Changes"}
        </button>
      </div>
    </div>
  );
}
