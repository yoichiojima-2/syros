"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChartColumn, LayoutDashboard, ListTree, ShieldCheck } from "lucide-react";
import { useOnline } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/theme-toggle";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/dashboard", label: "Dashboard", icon: ChartColumn },
  { href: "/sessions", label: "Sessions", icon: ListTree },
  { href: "/approvals", label: "Approvals", icon: ShieldCheck },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const online = useOnline();
  const [host, setHost] = useState("");
  useEffect(() => setHost(window.location.host), []);

  return (
    <div className="flex h-svh flex-col md:flex-row">
      <aside className="flex shrink-0 items-center gap-1 border-b border-border bg-surface px-3 py-2.5 md:w-56 md:flex-col md:items-stretch md:gap-0 md:border-r md:border-b-0 md:px-3 md:py-5">
        <div className="flex items-baseline gap-2 md:px-2 md:pb-5">
          <span className="size-2 shrink-0 self-center rounded-full bg-primary" />
          <span className="font-serif text-[17px] tracking-tight">syros</span>
          <span className="hidden truncate font-mono text-[11px] text-faint md:inline">{host}</span>
        </div>
        <nav className="flex gap-1 md:flex-col md:gap-0.5">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || (href === "/sessions" && pathname === "/session");
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition-colors",
                  active
                    ? "bg-primary-soft font-medium text-foreground"
                    : "text-muted-foreground hover:bg-secondary/70 hover:text-foreground",
                )}
              >
                <Icon className={cn("size-4", active && "text-primary")} />
                {label}
              </Link>
            );
          })}
        </nav>
        <span className="flex-1" />
        <div className="flex items-center gap-2 md:justify-between md:px-1 md:pt-4">
          <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className={cn("size-[7px] rounded-full", online ? "bg-ok" : "bg-destructive")} />
            <span className="hidden md:inline">{online ? "connected" : "offline"}</span>
          </span>
          <ThemeToggle />
        </div>
      </aside>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</main>
    </div>
  );
}
