"use client";

import { StatusBadge } from "@/components/status-badge";
import { createClient } from "@/lib/supabase/client";
import { useEffect, useState, useCallback } from "react";

type Channel = {
  id: string;
  channel_type: string;
  status: string;
  display_name: string | null;
  external_id: string | null;
  config: Record<string, unknown>;
  created_at: string;
};

export default function ChannelsSettingsPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const supabase = createClient();

  const load = useCallback(() => {
    setLoading(true);
    supabase
      .from("channels")
      .select("*")
      .order("created_at", { ascending: false })
      .then(({ data }) => {
        setChannels((data as Channel[]) || []);
        setLoading(false);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggle = async (ch: Channel) => {
    const next = ch.status === "active" ? "paused" : "active";
    await supabase.from("channels").update({ status: next }).eq("id", ch.id);
    load();
  };

  return (
    <div>
      <h1 className="text-2xl font-semibold text-foreground">Channels</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Configure your inbound communication channels.
      </p>

      <div className="mt-6 space-y-3">
        {loading ? (
          <div className="text-sm text-muted-foreground">Loading...</div>
        ) : channels.length === 0 ? (
          <div className="text-sm text-muted-foreground">No channels configured.</div>
        ) : (
          channels.map((ch) => (
            <div key={ch.id} className="flex items-center justify-between rounded-xl border border-border p-4 hover:bg-gray-50 transition">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-foreground">
                    {ch.display_name || ch.channel_type}
                  </span>
                  <StatusBadge status={ch.status} />
                  <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-600">{ch.channel_type}</span>
                </div>
                {ch.external_id && (
                  <p className="mt-1 text-xs text-muted-foreground">ID: {ch.external_id}</p>
                )}
              </div>
              <button
                onClick={() => toggle(ch)}
                className={`rounded-md px-4 py-1.5 text-xs font-medium transition ${
                  ch.status === "active"
                    ? "border border-border text-foreground hover:bg-red-50"
                    : "bg-green-600 text-white hover:bg-green-700"
                }`}
              >
                {ch.status === "active" ? "Pause" : "Activate"}
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
