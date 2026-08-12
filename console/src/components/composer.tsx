"use client";

import { useState } from "react";
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

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setText("");
    onSend(trimmed);
  };

  return (
    <form
      className="mx-auto flex w-full max-w-3xl gap-2 px-5 pt-3 pb-4"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Textarea
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder="Send a prompt — Enter to send, Shift+Enter for a new line"
        className="h-11 flex-1 resize-none font-mono"
      />
      <Button type="submit" disabled={disabled} className="h-11 rounded-lg px-4">
        Send
      </Button>
    </form>
  );
}
