import { Marked } from "marked";

// Chat-transcript markdown. A dedicated Marked instance (the artifact panel
// configures its own) with raw HTML escaped: the agent's prose gets headings,
// lists, and code styling, but markup it emits stays inert text — the string
// goes into the page via dangerouslySetInnerHTML, not a sandboxed iframe.

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const chatMarked = new Marked({
  renderer: {
    html: ({ text }) => escapeHtml(text),
  },
});

export function renderMarkdown(text: string): string {
  return chatMarked.parse(text, { async: false }) as string;
}
