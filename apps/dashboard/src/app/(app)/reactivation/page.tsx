"use client";

import { StatusBadge } from "@/components/status-badge";
import { createClient } from "@/lib/supabase/client";
import { timeAgo } from "@/lib/format";
import { Sparkles } from "lucide-react";
import { useEffect, useState, useCallback } from "react";

type Batch = {
  id: string;
  status: string;
  total: number;
  contacted: number;
  replied: number;
  converted: number;
  created_at: string;
};

type PreviewResult = { count: number; sample: { id: string; name: string | null; phone_e164: string | null; last_activity: string }[] };

export default function ReactivationPage() {
  const [batches, setBatches] = useState<Batch[]>([]);
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [daysInactive, setDaysInactive] = useState(60);
  const supabase = createClient();

  const loadBatches = useCallback(() => {
    setLoading(true);
    supabase
      .from("queue_jobs")
      .select("id, status, payload, created_at")
      .eq("queue", "reactivation")
      .order("created_at", { ascending: false })
      .limit(50)
      .then(({ data }) => {
        const mapped: Batch[] = (data || []).map((d: Record<string, unknown>) => {
          const p = (d.payload || {}) as Record<string, unknown>;
          return {
            id: d.id as string,
            status: d.status as string,
            total: (p.total as number) || 0,
            contacted: (p.contacted as number) || 0,
            replied: (p.replied as number) || 0,
            converted: (p.converted as number) || 0,
            created_at: d.created_at as string,
          };
        });
        setBatches(mapped);
        setLoading(false);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { loadBatches(); }, [loadBatches]);

  const doPreview = async () => {
    setPreviewing(true);
    const { data, error } = await supabase.rpc("select_reactivation_segment", {
      p_days_inactive: daysInactive,
      p_limit: 10,
    });
    if (!error && data) {
      setPreview({ count: (data as unknown[]).length, sample: data as PreviewResult["sample"] });
    }
    setPreviewing(false);
  };

  const doLaunch = async () => {
    if (!preview || preview.count === 0) return;
    setLaunching(true);
    await supabase.rpc("create_reactivation_batch", {
      p_days_inactive: daysInactive,
    });
    setLaunching(false);
    setPreview(null);
    loadBatches();
  };

  return (
    <div>
      <h1 className="text-2xl font-semibold text-foreground">Reactivation</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Re-engage stale leads and past customers with automated outreach.
      </p>

      <div className="mt-6 rounded-xl border border-border p-5">
        <h2 className="text-sm font-semibold text-foreground">New Campaign</h2>
        <div className="mt-3 flex flex-wrap items-end gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Days Inactive</label>
            <input
              type="number"
              min={14}
              max={365}
              value={daysInactive}
              onChange={(e) => setDaysInactive(Number(e.target.value))}
              className="w-28 rounded-md border border-border px-3 py-2 text-sm"
            />
          </div>
          <button
            onClick={doPreview}
            disabled={previewing}
            className="rounded-lg border border-border bg-white px-4 py-2 text-sm font-medium text-foreground shadow-sm transition hover:bg-gray-50 disabled:opacity-50"
          >
            {previewing ? "Previewing..." : "Preview Segment"}
          </button>
          {preview && preview.count > 0 && (
            <button
              onClick={doLaunch}
              disabled={launching}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
            >
              <Sparkles className="h-4 w-4" />
              {launching ? "Launching..." : `Launch (${preview.count} contacts)`}
            </button>
          )}
        </div>

        {preview && (
          <div className="mt-4">
            <p className="text-sm text-muted-foreground">
              Found <strong>{preview.count}</strong> contacts inactive for {daysInactive}+ days
            </p>
            {preview.sample.length > 0 && (
              <div className="mt-2 overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-border bg-gray-50">
                    <tr>
                      <th className="px-3 py-2 font-medium text-muted-foreground">Name</th>
                      <th className="px-3 py-2 font-medium text-muted-foreground">Phone</th>
                      <th className="px-3 py-2 font-medium text-muted-foreground">Last Activity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sample.map((c) => (
                      <tr key={c.id} className="border-b border-border last:border-b-0">
                        <td className="px-3 py-2 text-foreground">{c.name || "—"}</td>
                        <td className="px-3 py-2 text-foreground">{c.phone_e164 || "—"}</td>
                        <td className="px-3 py-2 text-muted-foreground">{timeAgo(c.last_activity)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="mt-8">
        <h2 className="text-sm font-semibold text-foreground">Campaign History</h2>
        <div className="mt-3 overflow-x-auto rounded-xl border border-border">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border bg-gray-50">
              <tr>
                <th className="px-4 py-3 font-medium text-muted-foreground">Status</th>
                <th className="px-4 py-3 font-medium text-muted-foreground">Total</th>
                <th className="px-4 py-3 font-medium text-muted-foreground">Contacted</th>
                <th className="px-4 py-3 font-medium text-muted-foreground">Replied</th>
                <th className="px-4 py-3 font-medium text-muted-foreground">Converted</th>
                <th className="px-4 py-3 font-medium text-muted-foreground">Created</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} className="p-4 text-muted-foreground">Loading...</td></tr>
              ) : batches.length === 0 ? (
                <tr><td colSpan={6} className="p-4 text-muted-foreground">No campaigns yet</td></tr>
              ) : (
                batches.map((b) => (
                  <tr key={b.id} className="border-b border-border last:border-b-0 hover:bg-gray-50 transition">
                    <td className="px-4 py-3"><StatusBadge status={b.status} /></td>
                    <td className="px-4 py-3 text-foreground">{b.total}</td>
                    <td className="px-4 py-3 text-foreground">{b.contacted}</td>
                    <td className="px-4 py-3 text-foreground">{b.replied}</td>
                    <td className="px-4 py-3 text-foreground">{b.converted}</td>
                    <td className="px-4 py-3 text-muted-foreground">{timeAgo(b.created_at)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
