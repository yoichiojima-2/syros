"use client";

import { useState } from "react";
import { ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function Composer({
  disabled,
  onSend,
}: {
  disabled: boolean;
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const empty = !text.trim();

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setText("");
    onSend(trimmed);
  };

  return (
    <form
      className="mx-auto w-full max-w-3xl px-5 pt-2 pb-5"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      {/* One rounded well holds the field and the send button, so the composer
          reads as a single surface rather than an input beside a button. */}
      <div className="rounded-2xl border border-input bg-card px-4 pt-3 pb-2.5 transition-colors focus-within:border-ring">
        <Textarea
          value={text}
          disabled={disabled}
          rows={2}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Send a prompt…"
          className="max-h-40 leading-relaxed"
        />
        <div className="flex items-center justify-between pt-1">
          <span className="text-[11px] text-faint">Enter to send · Shift+Enter for a new line</span>
          <Button type="submit" size="icon" disabled={disabled || empty} aria-label="Send prompt">
            <ArrowUp />
          </Button>
        </div>
      </div>
    </form>
  );
}
