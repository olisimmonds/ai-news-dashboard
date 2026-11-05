import { useParams } from "react-router-dom";

import { CATEGORY_BY_ID } from "../lib/categories";
import type { CategoryId } from "../lib/types";
import { FeedPage } from "./FeedPage";
import { NotFoundPage } from "./NotFoundPage";

export function CategoryPage() {
  const { categoryId } = useParams();
  const category = categoryId ? CATEGORY_BY_ID[categoryId as CategoryId] : undefined;

  if (!category) {
    return <NotFoundPage />;
  }

  return (
    <FeedPage
      title={category.label}
      eyebrow="The desk"
      description={category.description}
      category={category.id}
      showCategoryFilter={false}
      documentTitle={category.label}
    />
  );
}
