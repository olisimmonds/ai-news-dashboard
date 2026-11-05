import { CATEGORY_BY_ID } from "../lib/categories";
import type { CategoryId } from "../lib/types";

export function CategoryTag({ id }: { id: CategoryId }) {
  const category = CATEGORY_BY_ID[id];
  const label = category?.shortLabel ?? id;
  return (
    <span className={`category-tag category-${id}`}>
      <span className="category-dot" aria-hidden />
      {label}
    </span>
  );
}
