import type {
  Article,
  Briefing,
  BrowserFavourite,
  BrowserSession,
  BrowserStatus,
  Meta,
  SourceInfo,
  Stats,
  Story,
} from "../lib/types";

const NOW = new Date("2026-11-03T09:00:00.000Z");

export function makeArticle(overrides: Partial<Article> = {}): Article {
  return {
    id: 1,
    title: "Test lead headline",
    url: "https://example.com/story",
    source_id: "example",
    source_name: "Example Wire",
    summary: "A short summary of the story for testing.",
    published_at: new Date(NOW.getTime() - 45 * 60 * 1000).toISOString(),
    fetched_at: NOW.toISOString(),
    categories: ["ai-technology"],
    primary_category: "ai-technology",
    kind: "major",
    published_at_estimated: false,
    image_url: "",
    story_id: null,
    publisher_count: null,
    story_article_count: null,
    score: null,
    is_major: false,
    ...overrides,
  };
}

export function makeStory(overrides: Partial<Story> = {}): Story {
  return {
    ...makeArticle(overrides),
    story_id: overrides.story_id ?? 1,
    publisher_count: overrides.publisher_count ?? 1,
    story_article_count: overrides.story_article_count ?? 1,
    score: overrides.score ?? 10,
    is_major: overrides.is_major ?? false,
  };
}

export const stats: Stats = {
  total_articles: 42,
  category_counts: {
    "markets-energy": 12,
    "ai-technology": 18,
    general: 12,
  },
  enabled_sources: 50,
  healthy_sources: 47,
  last_refresh: {
    id: 7,
    trigger: "scheduled",
    started_at: new Date(NOW.getTime() - 10 * 60 * 1000).toISOString(),
    finished_at: new Date(NOW.getTime() - 8 * 60 * 1000).toISOString(),
    status: "partial",
    sources_ok: 47,
    sources_failed: 2,
    sources_not_modified: 1,
    articles_added: 12,
    articles_updated: 2,
    error_summary: "Two sources timed out.",
  },
  last_run: null,
};

export const sources: SourceInfo[] = [
  {
    id: "example",
    name: "Example Wire",
    url: "https://example.com",
    feed_url: "https://example.com/feed.xml",
    categories: ["ai-technology"],
    enabled: true,
    last_fetch_at: NOW.toISOString(),
    last_success_at: NOW.toISOString(),
    last_error: null,
    consecutive_failures: 0,
    healthy: true,
  },
  {
    id: "disabled-source",
    name: "Disabled Source",
    url: "https://disabled.example.com",
    feed_url: "https://disabled.example.com/feed.xml",
    categories: ["general"],
    enabled: false,
    last_fetch_at: null,
    last_success_at: null,
    last_error: null,
    consecutive_failures: 0,
    healthy: false,
  },
];

export const meta: Meta = {
  app_name: "Meridian",
  generated_at: NOW.toISOString(),
  categories: [
    { id: "markets-energy", label: "Markets & Energy", description: "", count: 12 },
    { id: "ai-technology", label: "AI & Technology", description: "", count: 18 },
    { id: "general", label: "General News", description: "", count: 12 },
  ],
  sources,
  stats,
  is_refreshing: false,
  next_refresh_at: new Date(NOW.getTime() + 20 * 60 * 1000).toISOString(),
  refresh_interval_minutes: 30,
  retention_days: 45,
};

const lead = makeStory({
  id: 1,
  title: "Test lead headline",
  kind: "routine",
  is_major: true,
  publisher_count: 3,
  story_article_count: 4,
});
const featured = makeStory({
  id: 2,
  title: "A featured story",
  categories: ["markets-energy"],
  primary_category: "markets-energy",
  kind: "company",
  source_name: "Market Wire",
});

export const briefing: Briefing = {
  lead,
  featured: [featured],
  sections: {
    "markets-energy": [featured],
    "ai-technology": [
      makeStory({ id: 4, title: "A research story", kind: "research", story_id: 4 }),
    ],
    general: [
      makeStory({
        id: 3,
        title: "A general news story",
        categories: ["general"],
        primary_category: "general",
        kind: "routine",
        source_name: "World Desk",
        story_id: 3,
      }),
    ],
  },
  latest: [makeStory({ id: 5, title: "The very latest story", kind: "opinion", story_id: 5 })],
  stats,
  generated_at: NOW.toISOString(),
};

export function makeBrowserSession(overrides: Partial<BrowserSession> = {}): BrowserSession {
  return {
    id: "session-1",
    created_at: NOW.toISOString(),
    last_activity_at: NOW.toISOString(),
    idle_timeout_minutes: 20,
    vnc_url: "/api/browser/sessions/session-1/vnc/meridian.html",
    ...overrides,
  };
}

export function makeBrowserStatus(overrides: Partial<BrowserStatus> = {}): BrowserStatus {
  return {
    enabled: true,
    authenticated: false,
    user: null,
    accounts_configured: true,
    docker_available: true,
    setup_message: null,
    max_sessions: 2,
    active_sessions: 0,
    idle_timeout_minutes: 20,
    session: null,
    ...overrides,
  };
}

export const browserFavourites: BrowserFavourite[] = [
  { title: "DuckDuckGo", url: "https://duckduckgo.com/" },
  { title: "Hacker News", url: "https://news.ycombinator.com/" },
];
