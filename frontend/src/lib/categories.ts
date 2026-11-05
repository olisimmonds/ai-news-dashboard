import type { ArticleKind, CategoryId } from "./types";

export interface CategoryDefinition {
  id: CategoryId;
  label: string;
  shortLabel: string;
  description: string;
  path: string;
}

export const CATEGORIES: CategoryDefinition[] = [
  {
    id: "markets-energy",
    label: "Markets & Energy",
    shortLabel: "Markets",
    description: "Markets, macroeconomics, energy, the grid and the companies building it.",
    path: "/category/markets-energy",
  },
  {
    id: "ai-technology",
    label: "AI & Technology",
    shortLabel: "AI & Tech",
    description: "Models, research, chips, data centres and the shape of enterprise technology.",
    path: "/category/ai-technology",
  },
  {
    id: "general",
    label: "General News",
    shortLabel: "World",
    description: "UK and international news, politics, science and the stories worth knowing.",
    path: "/category/general",
  },
];

export const CATEGORY_BY_ID: Record<CategoryId, CategoryDefinition> = {
  "markets-energy": CATEGORIES[0],
  "ai-technology": CATEGORIES[1],
  general: CATEGORIES[2],
};

export function categoryLabel(id: CategoryId): string {
  return CATEGORY_BY_ID[id]?.label ?? id;
}

export const KIND_LABELS: Record<ArticleKind, string> = {
  major: "Major",
  research: "Research",
  opinion: "Opinion",
  company: "Company",
  routine: "",
};
