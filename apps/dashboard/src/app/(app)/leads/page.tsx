"use client";

import { StatusBadge } from "@/components/status-badge";
import { createClient } from "@/lib/supabase/client";
import { formatCurrency, timeAgo } from "@/lib/format";
import { useEffect, useState } from "react";

type Lead = {
  id: string;
  stage: string;
  score: number | null;
  estimated_value: number | null;
  created_at: string;
  updated_at: string;
  contacts: { name: string | null; phone_e164: string | null; email: string | null } | null;
};

const stages = ["all", "new", "contacted", "qualified", "proposal", "won", "lost"] as const;

export default function LeadsPage() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [stage, setStage] = useState<string>("all");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const supabase = createClient();
    let q = supabase
      .from("leads")
      .select("id, stage, score, estimated_value, created_at, updated_at, contacts(name, phone_e164, email)")
      .order("updated_at", { ascending: false })
      .limit(100);
    if (stage !== "all") q = q.eq("stage", stage);
    q.then(({ data }) => {
      setLeads((data as unknown as Lead[]) || []);
      setLoading(false);
    });
  }, [stage]);

  return (
    <div>
      <h1 className="text-2xl font-semibold text-foreground">Leads</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Manage and track leads through the pipeline.
      </p>

      <div className="mt-6 flex flex-wrap gap-1">
        {stages.map((s) => (
          <button
            key={s}
            onClick={() => { setStage(s); setLoading(true); }}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              stage === s
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-gray-100"
            }`}
          >
            {s === "all" ? "All" : s.replace(/_/g, " ")}
          </button>
        ))}
      </div>

      <div className="mt-4 overflow-x-auto rounded-xl border border-border">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border bg-gray-50">
            <tr>
              <th className="px-4 py-3 font-medium text-muted-foreground">Contact</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Stage</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Score</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Value</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Updated</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={5} className="p-4 text-muted-foreground">Loading...</td></tr>
            ) : leads.length === 0 ? (
              <tr><td colSpan={5} className="p-4 text-muted-foreground">No leads found</td></tr>
            ) : (
              leads.map((l) => (
                <tr key={l.id} className="border-b border-border last:border-b-0 hover:bg-gray-50 transition">
                  <td className="px-4 py-3">
                    <p className="font-medium text-foreground">
                      {l.contacts?.name || l.contacts?.phone_e164 || "Unknown"}
                    </p>
                    {l.contacts?.email && (
                      <p className="text-xs text-muted-foreground">{l.contacts.email}</p>
                    )}
                  </td>
                  <td className="px-4 py-3"><StatusBadge status={l.stage} /></td>
                  <td className="px-4 py-3 text-foreground">{l.score ?? "—"}</td>
                  <td className="px-4 py-3 text-foreground">{formatCurrency(l.estimated_value)}</td>
                  <td className="px-4 py-3 text-muted-foreground">{timeAgo(l.updated_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
