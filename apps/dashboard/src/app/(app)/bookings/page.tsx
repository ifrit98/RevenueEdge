"use client";

import { StatusBadge } from "@/components/status-badge";
import { createClient } from "@/lib/supabase/client";
import { timeAgo } from "@/lib/format";
import { useEffect, useState, useCallback } from "react";

type Booking = {
  id: string;
  status: string;
  starts_at: string;
  ends_at: string | null;
  created_at: string;
  contacts: { name: string | null; phone_e164: string | null } | null;
  services: { name: string } | null;
};

const tabs = ["all", "confirmed", "tentative", "completed", "cancelled", "no_show"] as const;

export default function BookingsPage() {
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [tab, setTab] = useState<string>("all");
  const [loading, setLoading] = useState(true);
  const supabase = createClient();

  const load = useCallback(() => {
    setLoading(true);
    let q = supabase
      .from("bookings")
      .select("id, status, starts_at, ends_at, created_at, contacts(name, phone_e164), services(name)")
      .order("starts_at", { ascending: false })
      .limit(100);
    if (tab !== "all") q = q.eq("status", tab);
    q.then(({ data }) => {
      setBookings((data as unknown as Booking[]) || []);
      setLoading(false);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  useEffect(() => { load(); }, [load]);

  const cancel = async (id: string) => {
    const { error } = await supabase
      .from("bookings")
      .update({ status: "cancelled" })
      .eq("id", id);
    if (!error) load();
  };

  const fmtTime = (s: string) => {
    const d = new Date(s);
    return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }) +
      " " + d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  };

  return (
    <div>
      <h1 className="text-2xl font-semibold text-foreground">Bookings</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        View and manage scheduled appointments.
      </p>

      <div className="mt-6 flex flex-wrap gap-1">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              tab === t ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-gray-100"
            }`}
          >
            {t === "all" ? "All" : t.replace(/_/g, " ")}
          </button>
        ))}
      </div>

      <div className="mt-4 overflow-x-auto rounded-xl border border-border">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border bg-gray-50">
            <tr>
              <th className="px-4 py-3 font-medium text-muted-foreground">Contact</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Service</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Status</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Scheduled</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Created</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="p-4 text-muted-foreground">Loading...</td></tr>
            ) : bookings.length === 0 ? (
              <tr><td colSpan={6} className="p-4 text-muted-foreground">No bookings found</td></tr>
            ) : (
              bookings.map((b) => (
                <tr key={b.id} className="border-b border-border last:border-b-0 hover:bg-gray-50 transition">
                  <td className="px-4 py-3 font-medium text-foreground">
                    {b.contacts?.name || b.contacts?.phone_e164 || "—"}
                  </td>
                  <td className="px-4 py-3 text-foreground">{b.services?.name || "—"}</td>
                  <td className="px-4 py-3"><StatusBadge status={b.status} /></td>
                  <td className="px-4 py-3 text-foreground">{fmtTime(b.starts_at)}</td>
                  <td className="px-4 py-3 text-muted-foreground">{timeAgo(b.created_at)}</td>
                  <td className="px-4 py-3">
                    {(b.status === "confirmed" || b.status === "tentative") && (
                      <button
                        onClick={() => cancel(b.id)}
                        className="rounded-md border border-border px-3 py-1 text-xs font-medium text-foreground transition hover:bg-red-50"
                      >
                        Cancel
                      </button>
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
