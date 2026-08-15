"use client";

import { useState } from "react";
import { ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function Composer({
  disabled,
  onSend,
  placeholder = "Send a prompt…",
  className = "mx-auto w-full max-w-3xl px-5 pt-2 pb-5",
  autoFocus,
}: {
  disabled: boolean;
  /** Awaited when it returns a promise: the box stays disabled until it
   *  settles, and a rejection puts the text back rather than eating it. */
  onSend: (text: string) => void | Promise<unknown>;
  placeholder?: string;
  className?: string;
  autoFocus?: boolean;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const empty = !text.trim();

  const submit = async () => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setText("");
    setSending(true);
    try {
      await onSend(trimmed);
    } catch {
      setText(trimmed);
    } finally {
      setSending(false);
    }
  };

  return (
    <form
      className={className}
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
          disabled={disabled || sending}
          autoFocus={autoFocus}
          rows={2}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={placeholder}
          className="max-h-40 leading-relaxed"
        />
        <div className="flex items-center justify-between pt-1">
          <span className="text-[11px] text-faint">Enter to send · Shift+Enter for a new line</span>
          <Button
            type="submit"
            size="icon"
            disabled={disabled || sending || empty}
            aria-label="Send prompt"
          >
            <ArrowUp />
          </Button>
        </div>
      </div>
    </form>
  );
}
