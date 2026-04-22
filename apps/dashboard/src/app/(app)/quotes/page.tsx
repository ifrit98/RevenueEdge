"use client";

import { StatusBadge } from "@/components/status-badge";
import { createClient } from "@/lib/supabase/client";
import { formatCurrency, timeAgo } from "@/lib/format";
import { useEffect, useState, useCallback } from "react";

type Quote = {
  id: string;
  status: string;
  total_amount: number | null;
  line_items: unknown;
  valid_until: string | null;
  created_at: string;
  leads: { id: string; contacts: { name: string | null; phone_e164: string | null } | null } | null;
};

export default function QuotesPage() {
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [loading, setLoading] = useState(true);
  const supabase = createClient();

  const load = useCallback(() => {
    setLoading(true);
    supabase
      .from("quotes")
      .select("id, status, total_amount, line_items, valid_until, created_at, leads(id, contacts(name, phone_e164))")
      .order("created_at", { ascending: false })
      .limit(100)
      .then(({ data }) => {
        setQuotes((data as unknown as Quote[]) || []);
        setLoading(false);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { load(); }, [load]);

  const action = async (id: string, act: "approve" | "decline") => {
    const { error } = await supabase
      .from("quotes")
      .update({ status: act === "approve" ? "approved" : "void" })
      .eq("id", id);
    if (!error) load();
  };

  return (
    <div>
      <h1 className="text-2xl font-semibold text-foreground">Quotes</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Review, approve, and manage quotes.
      </p>

      <div className="mt-6 overflow-x-auto rounded-xl border border-border">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border bg-gray-50">
            <tr>
              <th className="px-4 py-3 font-medium text-muted-foreground">Contact</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Status</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Total</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Valid Until</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Created</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="p-4 text-muted-foreground">Loading...</td></tr>
            ) : quotes.length === 0 ? (
              <tr><td colSpan={6} className="p-4 text-muted-foreground">No quotes yet</td></tr>
            ) : (
              quotes.map((q) => (
                <tr key={q.id} className="border-b border-border last:border-b-0 hover:bg-gray-50 transition">
                  <td className="px-4 py-3 font-medium text-foreground">
                    {q.leads?.contacts?.name || q.leads?.contacts?.phone_e164 || "—"}
                  </td>
                  <td className="px-4 py-3"><StatusBadge status={q.status} /></td>
                  <td className="px-4 py-3 text-foreground">{formatCurrency(q.total_amount)}</td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {q.valid_until ? new Date(q.valid_until).toLocaleDateString() : "—"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{timeAgo(q.created_at)}</td>
                  <td className="px-4 py-3">
                    {q.status === "awaiting_review" && (
                      <div className="flex gap-2">
                        <button
                          onClick={() => action(q.id, "approve")}
                          className="rounded-md bg-green-600 px-3 py-1 text-xs font-medium text-white transition hover:bg-green-700"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => action(q.id, "decline")}
                          className="rounded-md border border-border px-3 py-1 text-xs font-medium text-foreground transition hover:bg-red-50"
                        >
                          Decline
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
