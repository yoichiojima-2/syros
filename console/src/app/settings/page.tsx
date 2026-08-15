"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { OptionsForm } from "@/components/options-form";
import { api, post } from "@/lib/api";
import type { OkResponse, SettingsResponse } from "@/lib/types";

// Global option defaults (serialized AgentOptions) — the base layer every
// workspace and session inherits. A workspace's stored options override these, and a
// run's explicit options override both.

export default function SettingsPage() {
  const [options, setOptions] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api<SettingsResponse>("/api/settings")
      .then((data) => {
        if (!cancelled) setOptions(data.options);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <h1 className="font-serif text-2xl tracking-tight">Settings</h1>
      <Card>
        <CardHeader>
          <CardTitle>Option defaults</CardTitle>
          <CardDescription>
            Inherited by every workspace and session — the skills installed here are the ones a
            session mounts unless a nearer layer installs its own. A workspace&apos;s stored options
            and a run&apos;s explicit options each override these, field by field.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error ? (
            <p className="text-[12px] text-destructive">{error}</p>
          ) : options === null ? (
            <div className="space-y-2">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : (
            <OptionsForm
              stored={options}
              submitLabel="Save settings"
              onSave={async (next) => {
                await post<OkResponse>("/api/settings", { options: next });
                return "saved";
              }}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
