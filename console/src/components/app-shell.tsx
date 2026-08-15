"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Bot,
  CalendarClock,
  ChartColumn,
  LayoutDashboard,
  Plug,
  ListTree,
  Package,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Users,
} from "lucide-react";
import { useOnline } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { PalettePicker } from "@/components/palette-picker";
import { ThemeToggle } from "@/components/theme-toggle";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/dashboard", label: "Dashboard", icon: ChartColumn },
  { href: "/sessions", label: "Sessions", icon: ListTree },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/workflows", label: "Workflows", icon: CalendarClock },
  { href: "/approvals", label: "Approvals", icon: ShieldCheck },
  { href: "/workspaces", label: "Workspaces", icon: Users },
  { href: "/skills", label: "Skills", icon: Sparkles },
  { href: "/connectors", label: "Connectors", icon: Plug },
  { href: "/artifacts", label: "Artifacts", icon: Package },
  { href: "/settings", label: "Settings", icon: SlidersHorizontal },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const online = useOnline();
  const [host, setHost] = useState("");
  useEffect(() => setHost(window.location.host), []);

  return (
    <div className="flex h-svh flex-col md:flex-row">
      <aside className="flex min-w-0 shrink-0 items-center gap-2 border-b border-border bg-surface px-3 py-2 md:w-56 md:flex-col md:items-stretch md:gap-0 md:border-r md:border-b-0 md:px-3 md:py-5">
        <div className="flex items-baseline gap-2 md:px-2 md:pb-5">
          <span className="size-2 shrink-0 self-center rounded-full bg-primary" />
          <span className="font-serif text-[17px] tracking-tight">syros</span>
          <span className="hidden truncate font-mono text-[11px] text-faint md:inline">{host}</span>
        </div>
        {/* below md the sidebar is a top bar: labels hide so ten entries fit as
            icons, and the row scrolls sideways on the narrowest screens */}
        <nav className="flex min-w-0 flex-1 gap-0.5 overflow-x-auto md:flex-none md:flex-col md:gap-0.5 md:overflow-visible">
          {NAV.map(({ href, label, icon: Icon }) => {
            // detail pages are singular (/session, /workflow, /workspace) and
            // keep their list's nav entry lit
            const active = pathname === href || href === `${pathname}s`;
            return (
              <Link
                key={href}
                href={href}
                title={label}
                className={cn(
                  "flex shrink-0 items-center gap-2.5 rounded-lg p-2 text-[13px] transition-colors md:px-2.5",
                  active
                    ? "bg-primary-soft font-medium text-foreground"
                    : "text-muted-foreground hover:bg-secondary/70 hover:text-foreground",
                )}
              >
                <Icon className={cn("size-4", active && "text-primary")} />
                <span className="hidden md:inline">{label}</span>
              </Link>
            );
          })}
        </nav>
        <span className="hidden flex-1 md:block" />
        <div className="flex shrink-0 items-center gap-2 md:justify-between md:px-1 md:pt-4">
          <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className={cn("size-[7px] rounded-full", online ? "bg-ok" : "bg-destructive")} />
            <span className="hidden md:inline">{online ? "connected" : "offline"}</span>
          </span>
          <span className="flex items-center">
            <PalettePicker />
            <ThemeToggle />
          </span>
        </div>
      </aside>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</main>
    </div>
  );
}
