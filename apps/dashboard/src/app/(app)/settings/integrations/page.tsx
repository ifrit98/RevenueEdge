"use client";

import { createClient } from "@/lib/supabase/client";
import { Calendar, CheckCircle, XCircle } from "lucide-react";
import { useEffect, useState } from "react";

type Business = {
  id: string;
  settings: Record<string, unknown>;
};

export default function IntegrationsSettingsPage() {
  const [biz, setBiz] = useState<Business | null>(null);
  const [loading, setLoading] = useState(true);
  const supabase = createClient();

  useEffect(() => {
    supabase
      .from("businesses")
      .select("id, settings")
      .limit(1)
      .single()
      .then(({ data }) => {
        setBiz(data as Business | null);
        setLoading(false);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const gcalConnected = !!biz?.settings?.google_calendar_refresh_token;

  const connectGcal = () => {
    window.location.href = `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080"}/v1/integrations/google-calendar/auth-url?business_id=${biz?.id}`;
  };

  const disconnectGcal = async () => {
    if (!biz) return;
    const settings = { ...biz.settings };
    delete settings.google_calendar_refresh_token;
    delete settings.google_calendar_access_token;
    delete settings.google_calendar_token_expiry;
    delete settings.google_calendar_id;
    await supabase.from("businesses").update({ settings }).eq("id", biz.id);
    setBiz({ ...biz, settings });
  };

  if (loading) return <div className="text-sm text-muted-foreground">Loading...</div>;

  return (
    <div className="max-w-lg">
      <h1 className="text-2xl font-semibold text-foreground">Integrations</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Connect external services to enhance the AI agent.
      </p>

      <div className="mt-6 space-y-4">
        <div className="rounded-xl border border-border p-5">
          <div className="flex items-start gap-4">
            <div className="rounded-lg bg-blue-50 p-2.5">
              <Calendar className="h-5 w-5 text-blue-600" />
            </div>
            <div className="flex-1">
              <h3 className="text-sm font-semibold text-foreground">Google Calendar</h3>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Auto-create bookings and check availability.
              </p>
              <div className="mt-3 flex items-center gap-3">
                {gcalConnected ? (
                  <>
                    <span className="flex items-center gap-1 text-xs font-medium text-green-600">
                      <CheckCircle className="h-3.5 w-3.5" /> Connected
                    </span>
                    <button
                      onClick={disconnectGcal}
                      className="rounded-md border border-border px-3 py-1 text-xs font-medium text-foreground transition hover:bg-red-50"
                    >
                      Disconnect
                    </button>
                  </>
                ) : (
                  <>
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <XCircle className="h-3.5 w-3.5" /> Not connected
                    </span>
                    <button
                      onClick={connectGcal}
                      className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90"
                    >
                      Connect
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-dashed border-border p-5 opacity-60">
          <p className="text-sm font-medium text-muted-foreground">
            More integrations coming soon (Stripe, QuickBooks, Zapier).
          </p>
        </div>
      </div>
    </div>
  );
}
