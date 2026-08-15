"""Session titles and summaries: one small model call at the end of a run.

The runner calls describe() just before releasing the session, so every run
refreshes the session's summary (and writes a title once) from what actually
happened — the prompts and the final result text. Routed like the session's
own model traffic: Vertex by default, the Anthropic API when the deployment
opted into that backend. Synchronous on purpose — the caller wraps in
asyncio.to_thread. Failures are the caller's to swallow: a session must
always release, described or not.
"""

from __future__ import annotations

import json
import os

from .options import AgentOptions

# Haiku: the cheapest current model — this is a two-line writing task.
ANTHROPIC_MODEL = "claude-haiku-4-5"
VERTEX_MODEL = "claude-haiku-4-5@20251001"

_EXCERPT = 4000  # chars of prompt/result context per side; plenty for a title

_INSTRUCTIONS = (
    "You are labelling an agent session for a dashboard. From the prompts and"
    ' final result below, reply with JSON only: {"title": ..., "summary": ...}.'
    " title: at most 8 words naming what the session is about (no trailing"
    " period). summary: one sentence, at most 25 words, saying what was done"
    " or found. Use the prompts' language."
)


def _client(options: AgentOptions):
    import anthropic

    # Anthropic API for now — the project has no Vertex Claude quota yet.
    # Once it does, route the "vertex" backend through AnthropicVertex with
    # VERTEX_MODEL instead of requiring the key.
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic(), ANTHROPIC_MODEL
    return anthropic.AnthropicVertex(
        project_id=options.resolved_project(),
        region=options.resolved_vertex_region(),
    ), VERTEX_MODEL


def fallback_title(prompts: list[str]) -> str | None:
    """First line of the first prompt, truncated — the no-model fallback."""
    for prompt in prompts:
        line = prompt.strip().splitlines()[0].strip() if prompt.strip() else ""
        if line:
            return line[:80]
    return None


def describe(
    options: AgentOptions, prompts: list[str], result: str | None
) -> dict[str, str | None]:
    """{"title": ..., "summary": ...} for the run. Raises on API failure."""
    context = "\n\n".join(
        [f"## Prompt\n{p[:_EXCERPT]}" for p in prompts]
        + ([f"## Final result\n{result[:_EXCERPT]}"] if result else [])
    )
    client, model = _client(options)
    response = client.messages.create(
        model=os.environ.get("SYROS_TITLE_MODEL") or model,
        max_tokens=200,
        system=_INSTRUCTIONS,
        messages=[{"role": "user", "content": context or "(empty session)"}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    start, end = text.find("{"), text.rfind("}")
    doc = json.loads(text[start : end + 1])
    title = str(doc.get("title") or "").strip() or None
    summary = str(doc.get("summary") or "").strip() or None
    return {"title": title and title[:120], "summary": summary and summary[:300]}
