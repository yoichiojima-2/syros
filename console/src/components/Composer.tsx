import { useState } from "react";

interface Props {
  disabled: boolean;
  onSend: (text: string) => void;
}

export default function Composer({ disabled, onSend }: Props) {
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
      <textarea
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
        className="h-11 flex-1 resize-none rounded-xl border border-line bg-panel px-3 py-2.5 font-mono text-[13px] text-ink transition-colors placeholder:text-faint focus:border-accent/60 disabled:opacity-40"
      />
      <button
        type="submit"
        disabled={disabled}
        className="rounded-xl bg-accent px-4 text-[13px] font-semibold text-bg transition-opacity hover:opacity-90 disabled:opacity-40"
      >
        Send
      </button>
    </form>
  );
}
