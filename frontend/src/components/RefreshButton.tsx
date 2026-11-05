import { useMeta } from "../hooks/useMeta";
import { Refresh } from "./icons";

export function RefreshButton({ compact = false }: { compact?: boolean }) {
  const { refreshing, refreshNews } = useMeta();

  return (
    <button
      type="button"
      className={compact ? "refresh-button is-compact" : "refresh-button"}
      onClick={() => void refreshNews()}
      disabled={refreshing}
      aria-label={refreshing ? "Refreshing news sources" : "Refresh news sources"}
    >
      <Refresh size={15} className={refreshing ? "spin" : undefined} />
      <span>{refreshing ? "Updating…" : "Refresh"}</span>
    </button>
  );
}
