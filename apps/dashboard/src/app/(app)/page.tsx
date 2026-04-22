"use client";

import { createClient } from "@/lib/supabase/client";
import { useEffect, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

type Snapshot = {
  metric_date: string;
  missed_calls: number;
  recovered_leads: number;
  inbound_leads: number;
  qualified_leads: number;
  quotes_sent: number;
  bookings: number;
  wins: number;
  attributed_revenue: number;
  payload: Record<string, unknown> | null;
};

const cards: { key: keyof Snapshot; label: string; color: string }[] = [
  { key: "missed_calls", label: "Missed Calls", color: "border-red-400 bg-red-50" },
  { key: "recovered_leads", label: "Recovered Leads", color: "border-green-400 bg-green-50" },
  { key: "inbound_leads", label: "Inbound Leads", color: "border-blue-400 bg-blue-50" },
  { key: "qualified_leads", label: "Qualified Leads", color: "border-indigo-400 bg-indigo-50" },
  { key: "quotes_sent", label: "Quotes Sent", color: "border-purple-400 bg-purple-50" },
  { key: "bookings", label: "Bookings", color: "border-teal-400 bg-teal-50" },
];

export default function DashboardPage() {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const supabase = createClient();
    supabase
      .from("metric_snapshots")
      .select("*")
      .order("metric_date", { ascending: false })
      .limit(30)
      .then(({ data }) => {
        setSnapshots((data as Snapshot[]) || []);
        setLoading(false);
      });
  }, []);

  const today = snapshots[0];
  const total = (key: keyof Snapshot) =>
    snapshots.reduce((s, r) => s + ((r[key] as number) || 0), 0);

  const chartData = [...snapshots]
    .reverse()
    .map((s) => ({
      date: s.metric_date.slice(5),
      missed: s.missed_calls,
      recovered: s.recovered_leads,
    }));

  return (
    <div>
      <h1 className="text-2xl font-semibold text-foreground">Dashboard</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Overview of your revenue recovery activity.
      </p>

      {loading ? (
        <div className="mt-8 grid grid-cols-2 gap-4 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl border bg-gray-50" />
          ))}
        </div>
      ) : (
        <>
          <div className="mt-8 grid grid-cols-2 gap-4 lg:grid-cols-3">
            {cards.map(({ key, label, color }) => (
              <div
                key={key}
                className={`rounded-xl border-l-4 p-4 ${color}`}
              >
                <p className="text-sm font-medium text-muted-foreground">{label}</p>
                <p className="mt-1 text-3xl font-bold text-foreground">
                  {(today?.[key] as number) ?? 0}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Last 30d: {total(key)} total
                </p>
              </div>
            ))}
          </div>

          {chartData.length > 1 && (
            <div className="mt-8 rounded-xl border border-border p-4">
              <h2 className="mb-4 text-sm font-semibold text-foreground">
                Missed vs Recovered — Last 30 Days
              </h2>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={chartData}>
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="missed" name="Missed" fill="#f87171" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="recovered" name="Recovered" fill="#4ade80" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </div>
  );
}
