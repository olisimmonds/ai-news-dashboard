import { KIND_LABELS } from "../lib/categories";
import type { ArticleKind } from "../lib/types";

export function KindBadge({ kind }: { kind: ArticleKind }) {
  const label = KIND_LABELS[kind];
  if (!label) return null;
  return <span className={`kind-badge kind-${kind}`}>{label}</span>;
}
