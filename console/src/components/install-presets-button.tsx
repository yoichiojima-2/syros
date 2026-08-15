"use client";

import { useState } from "react";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAction } from "@/lib/hooks";
import { post } from "@/lib/api";
import type { InstallPresetsResponse } from "@/lib/types";

// One button, three empty states (agents, workflows, workspaces). Installing
// creates the whole catalog rather than just the objects of the page you
// clicked from: the presets reference each other, so a workflow without its
// agents would not be installable anyway.
//
// Anything already present is skipped, so this is safe to click twice — which
// is why there is no confirmation and no --force equivalent here. Replacing
// edited presets is a CLI decision (`syros presets install --force`), not one
// to offer behind a button that looks like "get started".

export function InstallPresetsButton({
  onInstalled,
  className,
}: {
  onInstalled?: () => void;
  className?: string;
}) {
  const [flash, run] = useAction();
  const [busy, setBusy] = useState(false);

  const install = () =>
    run(async () => {
      setBusy(true);
      try {
        const result = await post<InstallPresetsResponse>("/api/presets/install", {});
        onInstalled?.();
        if (!result.installed.length) return "everything is already installed";
        const skipped = result.skipped.length ? `, ${result.skipped.length} already present` : "";
        return `installed ${result.installed.length} preset(s), ${result.files} file(s)${skipped}`;
      } finally {
        setBusy(false);
      }
    });

  return (
    <span className={className}>
      <Button variant="outline" size="sm" onClick={install} disabled={busy}>
        <Sparkles />
        {busy ? "Installing…" : "Install examples"}
      </Button>
      {flash && <span className="ml-3 text-[11px] text-muted-foreground">{flash}</span>}
    </span>
  );
}
