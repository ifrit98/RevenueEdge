"use client";

import { StatusBadge } from "@/components/status-badge";
import { createClient } from "@/lib/supabase/client";
import { timeAgo } from "@/lib/format";
import { useEffect, useState } from "react";

type Conversation = {
  id: string;
  status: string;
  channel_type: string;
  current_intent: string | null;
  current_urgency: string | null;
  confidence_score: number | null;
  ai_summary: string | null;
  last_message_at: string | null;
  contacts: { name: string | null; phone_e164: string | null } | null;
};

type Message = {
  id: string;
  direction: string;
  sender_type: string;
  body: string | null;
  created_at: string;
};

const tabs = ["all", "open", "awaiting_customer", "awaiting_human"] as const;

export default function InboxPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [tab, setTab] = useState<string>("all");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const supabase = createClient();
    let q = supabase
      .from("conversations")
      .select("id, status, channel_type, current_intent, current_urgency, confidence_score, ai_summary, last_message_at, contacts(name, phone_e164)")
      .order("last_message_at", { ascending: false })
      .limit(50);
    if (tab !== "all") q = q.eq("status", tab);
    q.then(({ data }) => {
      setConversations((data as unknown as Conversation[]) || []);
      setLoading(false);
    });
  }, [tab]);

  useEffect(() => {
    if (!selected) return;
    const supabase = createClient();
    supabase
      .from("messages")
      .select("id, direction, sender_type, body, created_at")
      .eq("conversation_id", selected.id)
      .order("created_at", { ascending: true })
      .limit(100)
      .then(({ data }) => setMessages((data as Message[]) || []));
  }, [selected]);

  const contactLabel = (c: Conversation) =>
    c.contacts?.name || c.contacts?.phone_e164 || "Unknown";

  return (
    <div className="flex h-[calc(100vh-4rem)] gap-0">
      {/* Left panel */}
      <div className="flex w-96 shrink-0 flex-col border-r border-border">
        <div className="flex gap-1 border-b border-border px-3 py-2">
          {tabs.map((t) => (
            <button
              key={t}
              onClick={() => { setTab(t); setSelected(null); }}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
                tab === t
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-gray-100"
              }`}
            >
              {t === "all" ? "All" : t.replace(/_/g, " ")}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="p-4 text-sm text-muted-foreground">Loading...</div>
          ) : conversations.length === 0 ? (
            <div className="p-4 text-sm text-muted-foreground">No conversations</div>
          ) : (
            conversations.map((c) => (
              <button
                key={c.id}
                onClick={() => setSelected(c)}
                className={`w-full border-b border-border px-4 py-3 text-left transition hover:bg-gray-50 ${
                  selected?.id === c.id ? "bg-blue-50" : ""
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="truncate text-sm font-medium text-foreground">
                    {contactLabel(c)}
                  </span>
                  <span className="ml-2 shrink-0 text-xs text-muted-foreground">
                    {c.last_message_at ? timeAgo(c.last_message_at) : ""}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <StatusBadge status={c.status} />
                  {c.current_intent && (
                    <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-600">
                      {c.current_intent.replace(/_/g, " ")}
                    </span>
                  )}
                </div>
                {c.ai_summary && (
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {c.ai_summary.slice(0, 80)}
                  </p>
                )}
              </button>
            ))
          )}
        </div>
      </div>

      {/* Right panel */}
      <div className="flex flex-1 flex-col">
        {!selected ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Select a conversation
          </div>
        ) : (
          <>
            <div className="border-b border-border px-6 py-4">
              <div className="flex items-center gap-3">
                <h2 className="text-lg font-semibold text-foreground">
                  {contactLabel(selected)}
                </h2>
                <StatusBadge status={selected.status} size="md" />
                <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                  {selected.channel_type}
                </span>
              </div>
              {(selected.current_intent || selected.current_urgency) && (
                <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
                  {selected.current_intent && <span>Intent: <strong>{selected.current_intent.replace(/_/g, " ")}</strong></span>}
                  {selected.current_urgency && <span>Urgency: <strong>{selected.current_urgency}</strong></span>}
                  {selected.confidence_score != null && (
                    <span>
                      Confidence:{" "}
                      <strong className={selected.confidence_score >= 0.82 ? "text-green-600" : selected.confidence_score >= 0.72 ? "text-yellow-600" : "text-red-600"}>
                        {(selected.confidence_score * 100).toFixed(0)}%
                      </strong>
                    </span>
                  )}
                </div>
              )}
              {selected.ai_summary && (
                <p className="mt-2 rounded-md bg-gray-50 px-3 py-2 text-xs text-muted-foreground">
                  {selected.ai_summary}
                </p>
              )}
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-4">
              {messages.length === 0 ? (
                <p className="text-sm text-muted-foreground">No messages yet</p>
              ) : (
                <div className="space-y-3">
                  {messages.map((m) => (
                    <div
                      key={m.id}
                      className={`flex ${m.direction === "outbound" ? "justify-end" : "justify-start"}`}
                    >
                      <div
                        className={`max-w-md rounded-xl px-4 py-2.5 ${
                          m.direction === "outbound"
                            ? "bg-primary text-primary-foreground"
                            : "bg-gray-100 text-foreground"
                        }`}
                      >
                        <p className="text-sm whitespace-pre-wrap">{m.body || "(no content)"}</p>
                        <p className={`mt-1 text-xs ${m.direction === "outbound" ? "text-blue-200" : "text-muted-foreground"}`}>
                          {m.sender_type} · {timeAgo(m.created_at)}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
