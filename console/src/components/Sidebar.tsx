import type { SessionSummary } from "../types";
import { cost, relTime, shortId, stateDotClass } from "../format";

interface Props {
  sessions: SessionSummary[] | null;
  activeId: string | null;
  now: number;
  onSelect: (sid: string) => void;
}

export default function Sidebar({ sessions, activeId, now, onSelect }: Props) {
  return (
    <nav className="flex min-h-0 flex-col overflow-y-auto border-r border-line bg-panel px-2.5 py-3">
      <div className="flex items-baseline gap-2 px-2 pb-2">
        <span className="font-mono text-[10px] font-semibold tracking-[0.14em] text-dim uppercase">
          Sessions
        </span>
        {sessions && sessions.length > 0 && (
          <span className="font-mono text-[10px] text-faint">{sessions.length}</span>
        )}
      </div>
      {sessions === null ? (
        <p className="px-2 text-[13px] text-dim">loading…</p>
      ) : sessions.length === 0 ? (
        <p className="px-2 text-[13px] text-dim">No sessions yet.</p>
      ) : (
        <ul className="space-y-0.5">
          {sessions.map((s) => (
            <li key={s.id}>
              <button
                type="button"
                title={s.id}
                onClick={() => onSelect(s.id)}
                className={`w-full rounded-lg border px-2.5 py-2 text-left font-mono transition-colors ${
                  s.id === activeId
                    ? "border-line bg-raised"
                    : "border-transparent hover:bg-raised/60"
                }`}
              >
                <span className="flex items-center gap-2 text-xs text-ink">
                  <span
                    className={`size-[7px] flex-none rounded-full ${stateDotClass(s.state)}`}
                  />
                  <span className="truncate">{shortId(s.id)}</span>
                </span>
                <span className="mt-0.5 flex gap-2.5 text-[11px] text-dim">
                  <span>{cost(s.cost_usd)}</span>
                  <span>{relTime(s.updated_at || s.created_at, now)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
