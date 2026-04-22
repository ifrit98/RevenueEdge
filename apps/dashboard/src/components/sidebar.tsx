"use client";

import { createClient } from "@/lib/supabase/client";
import type { User } from "@supabase/supabase-js";
import {
  BookOpen,
  Building2,
  Calendar,
  FileText,
  Inbox,
  LayoutDashboard,
  LogOut,
  Plug,
  Radio,
  Sparkles,
  Users,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ComponentType } from "react";
import { useCallback, useEffect, useState } from "react";

type NavItem = { href: string; label: string; icon: ComponentType<{ className?: string }> };

const mainNav: NavItem[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/inbox", label: "Inbox", icon: Inbox },
  { href: "/leads", label: "Leads", icon: Users },
  { href: "/quotes", label: "Quotes", icon: FileText },
  { href: "/bookings", label: "Bookings", icon: Calendar },
  { href: "/reactivation", label: "Reactivation", icon: Sparkles },
  { href: "/knowledge", label: "Knowledge", icon: BookOpen },
];

const settingsSub: { href: string; label: string; icon: ComponentType<{ className?: string }> }[] = [
  { href: "/settings/business", label: "Business", icon: Building2 },
  { href: "/settings/channels", label: "Channels", icon: Radio },
  { href: "/settings/services", label: "Services", icon: Wrench },
  { href: "/settings/integrations", label: "Integrations", icon: Plug },
];

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function businessLabel(user: User | null) {
  if (!user) return "Revenue Edge";
  const meta = user.user_metadata as Record<string, string | undefined> | undefined;
  const fromMeta = meta?.business_name ?? meta?.company_name ?? meta?.full_name;
  if (fromMeta && typeof fromMeta === "string") return fromMeta;
  if (user.email) return user.email.split("@")[0] ?? "Your business";
  return "Your business";
}

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    const supabase = createClient();
    let cancelled = false;
    void supabase.auth.getUser().then(({ data: { user: u } }) => {
      if (!cancelled) setUser(u);
    });
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user ?? null);
    });
    return () => {
      cancelled = true;
      subscription.unsubscribe();
    };
  }, []);

  const signOut = useCallback(async () => {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }, [router]);

  return (
    <aside className="flex h-screen w-64 flex-col border-r border-border bg-gray-50">
      <div className="border-b border-border px-4 py-5">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Business</p>
        <p className="mt-1 truncate text-sm font-semibold text-foreground" title={businessLabel(user)}>
          {businessLabel(user)}
        </p>
      </div>
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        <ul className="space-y-1">
          {mainNav.map(({ href, label, icon: Icon }) => {
            const active = isActive(pathname, href);
            return (
              <li key={href}>
                <Link
                  href={href}
                  className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition ${
                    active
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:bg-white hover:text-foreground"
                  }`}
                >
                  <Icon className="h-4 w-4 shrink-0" aria-hidden />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
        <div className="mt-6">
          <p className="mb-2 px-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Settings
          </p>
          <ul className="space-y-1">
            {settingsSub.map(({ href, label, icon: Icon }) => {
              const active = isActive(pathname, href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition ${
                      active
                        ? "bg-primary/10 text-primary"
                        : "text-muted-foreground hover:bg-white hover:text-foreground"
                    }`}
                  >
                    <Icon className="h-4 w-4 shrink-0" aria-hidden />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      </nav>
      <div className="border-t border-border p-3">
        <button
          type="button"
          onClick={() => void signOut()}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-white px-3 py-2 text-sm font-medium text-foreground shadow-sm transition hover:bg-accent"
        >
          <LogOut className="h-4 w-4" aria-hidden />
          Sign out
        </button>
      </div>
    </aside>
  );
}
