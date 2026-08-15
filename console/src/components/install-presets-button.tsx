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

// No onInstalled/refresh callback on purpose. This button only ever renders
// inside an empty state, so refreshing unmounts it — along with the flash it
// was about to show, which matters most when that flash is an error. Every list
// hook polls (4-5s), so the rows arrive on their own and the outcome stays
// readable until they do.
export function InstallPresetsButton() {
  const [flash, run] = useAction();
  const [busy, setBusy] = useState(false);

  const install = () =>
    run(async () => {
      setBusy(true);
      try {
        const result = await post<InstallPresetsResponse>("/api/presets/install", {});
        if (!result.installed.length) return "everything is already installed";
        const kept = result.kept ? `, ${result.kept} existing file(s) kept` : "";
        return `installed ${result.installed.length} preset(s), ${result.files} file(s)${kept}`;
      } finally {
        setBusy(false);
      }
    });

  return (
    <span>
      <Button variant="outline" size="sm" onClick={install} disabled={busy}>
        <Sparkles />
        {busy ? "Installing…" : "Install examples"}
      </Button>
      {flash && <span className="ml-3 text-[11px] text-muted-foreground">{flash}</span>}
    </span>
  );
}
