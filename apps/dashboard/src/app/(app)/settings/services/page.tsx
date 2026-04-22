"use client";

import { createClient } from "@/lib/supabase/client";
import { formatCurrency } from "@/lib/format";
import { Plus, X } from "lucide-react";
import { useEffect, useState, useCallback } from "react";

type Service = {
  id: string;
  name: string;
  description: string | null;
  base_price_low: number | null;
  base_price_high: number | null;
  required_intake_fields: string[];
  active: boolean;
  tags: string[];
};

export default function ServicesSettingsPage() {
  const [services, setServices] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    name: "", description: "", base_price_low: "", base_price_high: "", required_intake_fields: "", tags: "",
  });
  const [saving, setSaving] = useState(false);
  const supabase = createClient();

  const load = useCallback(() => {
    setLoading(true);
    supabase
      .from("services")
      .select("*")
      .order("name")
      .then(({ data }) => {
        setServices((data as Service[]) || []);
        setLoading(false);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setSaving(true);
    await supabase.from("services").insert({
      name: form.name,
      description: form.description || null,
      base_price_low: form.base_price_low ? Number(form.base_price_low) : null,
      base_price_high: form.base_price_high ? Number(form.base_price_high) : null,
      required_intake_fields: form.required_intake_fields ? form.required_intake_fields.split(",").map((s) => s.trim()) : [],
      tags: form.tags ? form.tags.split(",").map((s) => s.trim()) : [],
    });
    setSaving(false);
    setShowForm(false);
    setForm({ name: "", description: "", base_price_low: "", base_price_high: "", required_intake_fields: "", tags: "" });
    load();
  };

  const toggleActive = async (svc: Service) => {
    await supabase.from("services").update({ active: !svc.active }).eq("id", svc.id);
    load();
  };

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Services</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Define your business services for quoting and booking.
          </p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90"
        >
          {showForm ? <X className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
          {showForm ? "Cancel" : "Add Service"}
        </button>
      </div>

      {showForm && (
        <div className="mt-4 rounded-xl border border-border p-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Name</label>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full rounded-md border border-border px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Description</label>
              <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
                className="w-full rounded-md border border-border px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Price Range Low</label>
              <input type="number" value={form.base_price_low} onChange={(e) => setForm({ ...form, base_price_low: e.target.value })}
                className="w-full rounded-md border border-border px-3 py-2 text-sm" placeholder="0" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Price Range High</label>
              <input type="number" value={form.base_price_high} onChange={(e) => setForm({ ...form, base_price_high: e.target.value })}
                className="w-full rounded-md border border-border px-3 py-2 text-sm" placeholder="0" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Required Intake Fields (comma-separated)</label>
              <input value={form.required_intake_fields} onChange={(e) => setForm({ ...form, required_intake_fields: e.target.value })}
                className="w-full rounded-md border border-border px-3 py-2 text-sm" placeholder="address, phone, sqft" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">Tags (comma-separated)</label>
              <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })}
                className="w-full rounded-md border border-border px-3 py-2 text-sm" placeholder="popular, seasonal" />
            </div>
            <div className="flex items-end sm:col-span-2">
              <button onClick={save} disabled={saving || !form.name.trim()}
                className="rounded-lg bg-primary px-6 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50">
                {saving ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="mt-6 overflow-x-auto rounded-xl border border-border">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border bg-gray-50">
            <tr>
              <th className="px-4 py-3 font-medium text-muted-foreground">Name</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Price Range</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Intake Fields</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Status</th>
              <th className="px-4 py-3 font-medium text-muted-foreground">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={5} className="p-4 text-muted-foreground">Loading...</td></tr>
            ) : services.length === 0 ? (
              <tr><td colSpan={5} className="p-4 text-muted-foreground">No services configured</td></tr>
            ) : (
              services.map((s) => (
                <tr key={s.id} className="border-b border-border last:border-b-0 hover:bg-gray-50 transition">
                  <td className="px-4 py-3">
                    <p className="font-medium text-foreground">{s.name}</p>
                    {s.description && <p className="text-xs text-muted-foreground">{s.description}</p>}
                  </td>
                  <td className="px-4 py-3 text-foreground">
                    {s.base_price_low != null || s.base_price_high != null
                      ? `${formatCurrency(s.base_price_low)} – ${formatCurrency(s.base_price_high)}`
                      : "—"}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {(s.required_intake_fields || []).map((f) => (
                        <span key={f} className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-600">{f}</span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                      s.active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"
                    }`}>
                      {s.active ? "active" : "inactive"}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <button onClick={() => toggleActive(s)}
                      className={`rounded-md px-3 py-1 text-xs font-medium transition ${
                        s.active ? "border border-border text-foreground hover:bg-red-50" : "bg-green-600 text-white hover:bg-green-700"
                      }`}>
                      {s.active ? "Deactivate" : "Activate"}
                    </button>
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
