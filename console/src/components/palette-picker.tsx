"use client";

import { useEffect, useState } from "react";
import { Check, SwatchBook } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Slack-style theme picker: each palette re-skins the CSS tokens via a
// `data-palette` attribute on <html> (see globals.css). The choice persists
// in localStorage and is re-applied before first paint by the inline script
// in layout.tsx. Swatches show each palette's light accent on its page tone.
export const PALETTE_KEY = "syros-palette";

const PALETTES = [
  { id: "", label: "Clay", accent: "#c96442", page: "#faf9f5" },
  { id: "harbour-haze", label: "Harbour Haze", accent: "#5a7d94", page: "#f3f6f7" },
  { id: "stone-path", label: "Stone Path", accent: "#7d7364", page: "#f4f3f0" },
  { id: "coastal-morning", label: "Coastal Morning", accent: "#37587a", page: "#f9f8f3" },
  { id: "ink-wash", label: "Ink Wash", accent: "#2b2b28", page: "#f7f6f2" },
  { id: "lotus-garden", label: "Lotus Garden", accent: "#8a79ab", page: "#f6f6f2" },
];

function applyPalette(id: string) {
  if (id) {
    document.documentElement.dataset.palette = id;
    localStorage.setItem(PALETTE_KEY, id);
  } else {
    delete document.documentElement.dataset.palette;
    localStorage.removeItem(PALETTE_KEY);
  }
}

export function PalettePicker() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  useEffect(() => setCurrent(localStorage.getItem(PALETTE_KEY) ?? ""), []);

  return (
    <div className="relative">
      <Button
        variant="ghost"
        size="icon"
        aria-label="Choose color theme"
        onClick={() => setOpen((v) => !v)}
      >
        <SwatchBook className="size-4" />
      </Button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          {/* below the bar on mobile (top-bar layout), above the button on md+ */}
          <div className="absolute right-0 top-full z-50 mt-2 w-48 rounded-lg border border-border bg-card p-1 shadow-lg md:top-auto md:right-auto md:bottom-full md:left-0 md:mt-0 md:mb-2">
            {PALETTES.map((palette) => (
              <button
                key={palette.id}
                onClick={() => {
                  applyPalette(palette.id);
                  setCurrent(palette.id);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors",
                  palette.id === current
                    ? "bg-secondary text-foreground"
                    : "text-muted-foreground hover:bg-secondary/70 hover:text-foreground",
                )}
              >
                <span
                  className="flex size-4 shrink-0 items-center justify-center rounded-full border border-border"
                  style={{ background: palette.page }}
                >
                  <span className="size-2 rounded-full" style={{ background: palette.accent }} />
                </span>
                <span className="flex-1">{palette.label}</span>
                {palette.id === current && <Check className="size-3.5 text-primary" />}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
