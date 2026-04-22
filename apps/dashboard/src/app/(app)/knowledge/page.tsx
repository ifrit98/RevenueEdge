"use client";

import { createClient } from "@/lib/supabase/client";
import { timeAgo } from "@/lib/format";
import { Plus, X } from "lucide-react";
import { useEffect, useState, useCallback } from "react";

type KnowledgeItem = {
  id: string;
  type: string;
  title: string | null;
  content: string;
  source: string | null;
  created_at: string;
};

const types = ["all", "faq", "policy", "procedure", "pricing", "general"] as const;

export default function KnowledgePage() {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ type: "faq", title: "", content: "", source: "" });
  const [saving, setSaving] = useState(false);
  const supabase = createClient();

  const load = useCallback(() => {
    setLoading(true);
    let q = supabase
      .from("knowledge_items")
      .select("id, type, title, content, source, created_at")
      .order("created_at", { ascending: false })
      .limit(100);
    if (typeFilter !== "all") q = q.eq("type", typeFilter);
    q.then(({ data }) => {
      setItems((data as KnowledgeItem[]) || []);
      setLoading(false);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typeFilter]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setSaving(true);
    const { error } = await supabase
      .from("knowledge_items")
      .insert({ type: form.type, title: form.title || null, content: form.content, source: form.source || null });
    setSaving(false);
    if (!error) {
      setShowForm(false);
      setForm({ type: "faq", title: "", content: "", source: "" });
      load();
    }
  };

  const remove = async (id: string) => {
    await supabase.from("knowledge_items").delete().eq("id", id);
    load();
  };

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Knowledge Base</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            FAQs, policies, pricing, and procedures the AI agent uses.
          </p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90"
        >
          {showForm ? <X className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
          {showForm ? "Cancel" : "Add Item"}
        </button>
      </div>

      {showForm && (
        <div className="mt-4 rounded-xl border border-border p-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Type</label>
              <select
                value={form.type}
                onChange={(e) => setForm({ ...form, type: e.target.value })}
                className="w-full rounded-md border border-border px-3 py-2 text-sm"
              >
                {types.filter((t) => t !== "all").map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Title</label>
              <input
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                className="w-full rounded-md border border-border px-3 py-2 text-sm"
                placeholder="e.g. Cancellation policy"
              />
            </div>
            <div className="sm:col-span-2">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Content</label>
              <textarea
                value={form.content}
                onChange={(e) => setForm({ ...form, content: e.target.value })}
                rows={4}
                className="w-full rounded-md border border-border px-3 py-2 text-sm"
                placeholder="Write the knowledge content..."
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Source (optional)</label>
              <input
                value={form.source}
                onChange={(e) => setForm({ ...form, source: e.target.value })}
                className="w-full rounded-md border border-border px-3 py-2 text-sm"
                placeholder="e.g. website, manual"
              />
            </div>
            <div className="flex items-end">
              <button
                onClick={save}
                disabled={saving || !form.content.trim()}
                className="rounded-lg bg-primary px-6 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
              >
                {saving ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="mt-6 flex flex-wrap gap-1">
        {types.map((t) => (
          <button
            key={t}
            onClick={() => setTypeFilter(t)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              typeFilter === t ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-gray-100"
            }`}
          >
            {t === "all" ? "All" : t}
          </button>
        ))}
      </div>

      <div className="mt-4 space-y-3">
        {loading ? (
          <div className="text-sm text-muted-foreground">Loading...</div>
        ) : items.length === 0 ? (
          <div className="text-sm text-muted-foreground">No knowledge items yet. Add your first one above.</div>
        ) : (
          items.map((item) => (
            <div key={item.id} className="rounded-xl border border-border p-4 hover:bg-gray-50 transition">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">
                      {item.type}
                    </span>
                    {item.title && (
                      <h3 className="text-sm font-semibold text-foreground">{item.title}</h3>
                    )}
                  </div>
                  <p className="mt-2 text-sm text-foreground whitespace-pre-wrap">
                    {item.content.length > 300 ? item.content.slice(0, 300) + "..." : item.content}
                  </p>
                  <div className="mt-2 flex gap-3 text-xs text-muted-foreground">
                    {item.source && <span>Source: {item.source}</span>}
                    <span>{timeAgo(item.created_at)}</span>
                  </div>
                </div>
                <button
                  onClick={() => remove(item.id)}
                  className="ml-3 shrink-0 rounded-md border border-border p-1.5 text-muted-foreground transition hover:bg-red-50 hover:text-red-600"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
