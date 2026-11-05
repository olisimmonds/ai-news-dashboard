export type CategoryId = "markets-energy" | "ai-technology" | "general";
export type SortOrder = "newest" | "oldest";
export type ArticleKind = "major" | "research" | "opinion" | "company" | "routine";

export interface Article {
  id: number;
  title: string;
  url: string;
  source_id: string;
  source_name: string;
  summary: string;
  published_at: string;
  fetched_at: string;
  categories: CategoryId[];
  primary_category: CategoryId;
  kind: ArticleKind;
  published_at_estimated: boolean;
  image_url: string;
  story_id: number | null;
  publisher_count: number | null;
  story_article_count: number | null;
  score: number | null;
  is_major: boolean;
}

export interface Story extends Article {
  story_id: number;
  publisher_count: number;
  story_article_count: number;
  score: number;
  is_major: boolean;
}

export interface ArticleList {
  items: Article[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface CategoryInfo {
  id: CategoryId;
  label: string;
  description: string;
  count: number;
}

export interface SourceInfo {
  id: string;
  name: string;
  url: string;
  feed_url: string;
  categories: CategoryId[];
  enabled: boolean;
  last_fetch_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  healthy: boolean;
}

export interface RefreshRun {
  id: number;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "success" | "partial" | "failed";
  sources_ok: number;
  sources_failed: number;
  sources_not_modified: number;
  articles_added: number;
  articles_updated: number;
  error_summary: string | null;
}

export interface Stats {
  total_articles: number;
  category_counts: Record<CategoryId, number>;
  enabled_sources: number;
  healthy_sources: number;
  last_refresh: RefreshRun | null;
  last_run: RefreshRun | null;
}

export interface Meta {
  app_name: string;
  generated_at: string;
  categories: CategoryInfo[];
  sources: SourceInfo[];
  stats: Stats;
  is_refreshing: boolean;
  next_refresh_at: string | null;
  refresh_interval_minutes: number;
  retention_days: number;
}

export interface Briefing {
  lead: Story | null;
  featured: Story[];
  sections: Record<CategoryId, Story[]>;
  latest: Story[];
  stats: Stats;
  generated_at: string;
}

export interface WeatherLocation {
  id: "london" | "winchester";
  name: string;
  available: boolean;
  observed_at: string | null;
  local_date: string | null;
  temperature: number | null;
  feels_like: number | null;
  condition: string | null;
  weather_code: number | null;
  high: number | null;
  low: number | null;
  rain_chance: number | null;
  error: string | null;
}

export interface Weather {
  locations: WeatherLocation[];
  generated_at: string;
}

export interface ArticleQuery {
  category?: CategoryId;
  source?: string;
  kind?: ArticleKind;
  q?: string;
  sort?: SortOrder;
  limit?: number;
  offset?: number;
  ids?: number[];
}
