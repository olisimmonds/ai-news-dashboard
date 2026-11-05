import { useMeta } from "../hooks/useMeta";
import { formatRelativeTime, formatSourceCount, formatStoryCount } from "../lib/format";

export function DeskStatus() {
  const { meta, refreshing } = useMeta();
  const stats = meta?.stats;
  const lastRefresh = stats?.last_refresh;

  return (
    <div className="status-line" aria-live="polite">
      <span
        className={`status-dot${
          refreshing ? " is-updating" : lastRefresh?.status === "failed" ? " is-stale" : " is-live"
        }`}
        aria-hidden
      />
      <span>
        {refreshing
          ? "Updating sources…"
          : lastRefresh
            ? `Updated ${formatRelativeTime(lastRefresh.started_at)}`
            : "Awaiting first update"}
      </span>
      {stats ? (
        <>
          <span className="status-sep" aria-hidden>
            ·
          </span>
          <span>{formatStoryCount(stats.total_articles)}</span>
          <span className="status-sep" aria-hidden>
            ·
          </span>
          <span>{formatSourceCount(stats.enabled_sources)}</span>
        </>
      ) : null}
    </div>
  );
}
