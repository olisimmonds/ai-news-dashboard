import type {
  ArticleList,
  ArticleQuery,
  Briefing,
  BrowserAction,
  BrowserFavourite,
  BrowserSession,
  BrowserStatus,
  Meta,
  Weather,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      ...init,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError("Could not reach the news server. Check that it is running.", 0);
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (typeof payload.detail === "string") {
        detail = payload.detail;
      }
    } catch {
      // Keep the status-based message when the body is not JSON.
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

function queryString(query: ArticleQuery): string {
  const params = new URLSearchParams();
  if (query.category) params.set("category", query.category);
  if (query.source) params.set("source_id", query.source);
  if (query.kind) params.set("kind", query.kind);
  if (query.q) params.set("q", query.q);
  if (query.sort) params.set("sort", query.sort);
  if (query.limit) params.set("limit", String(query.limit));
  if (query.offset) params.set("offset", String(query.offset));
  if (query.ids?.length) params.set("ids", query.ids.join(","));
  const value = params.toString();
  return value ? `?${value}` : "";
}

export function getArticles(
  query: ArticleQuery = {},
  init?: { signal?: AbortSignal },
): Promise<ArticleList> {
  return request<ArticleList>(`/api/articles${queryString(query)}`, { signal: init?.signal });
}

export function getBriefing(init?: { signal?: AbortSignal }): Promise<Briefing> {
  return request<Briefing>("/api/briefing", { signal: init?.signal });
}

export function getMeta(init?: { signal?: AbortSignal }): Promise<Meta> {
  return request<Meta>("/api/meta", { signal: init?.signal });
}

export function getWeather(init?: { signal?: AbortSignal }): Promise<Weather> {
  return request<Weather>("/api/weather", { signal: init?.signal });
}

export function triggerRefresh(): Promise<{ status: string; message: string }> {
  return request<{ status: string; message: string }>("/api/refresh", {
    method: "POST",
  });
}

export function getBrowserStatus(init?: { signal?: AbortSignal }): Promise<BrowserStatus> {
  return request<BrowserStatus>("/api/browser/status", { signal: init?.signal });
}

export function loginBrowser(username: string, password: string): Promise<{ user: string }> {
  return request<{ user: string }>("/api/browser/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export function logoutBrowser(): Promise<{ status: string }> {
  return request<{ status: string }>("/api/browser/auth/logout", { method: "POST" });
}

export function getBrowserFavourites(init?: { signal?: AbortSignal }): Promise<BrowserFavourite[]> {
  return request<BrowserFavourite[]>("/api/browser/favourites", { signal: init?.signal });
}

export function saveBrowserFavourites(favourites: BrowserFavourite[]): Promise<BrowserFavourite[]> {
  return request<BrowserFavourite[]>("/api/browser/favourites", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ favourites }),
  });
}

export function startBrowserSession(url?: string): Promise<BrowserSession> {
  return request<BrowserSession>("/api/browser/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: url || null }),
  });
}

export function navigateBrowserSession(
  sessionId: string,
  url: string,
  newTab = false,
): Promise<BrowserSession> {
  return request<BrowserSession>(`/api/browser/sessions/${sessionId}/navigate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, new_tab: newTab }),
  });
}

export function browserSessionAction(
  sessionId: string,
  action: BrowserAction,
): Promise<BrowserSession> {
  return request<BrowserSession>(`/api/browser/sessions/${sessionId}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
}

export function restartBrowserSession(sessionId: string): Promise<BrowserSession> {
  return request<BrowserSession>(`/api/browser/sessions/${sessionId}/restart`, {
    method: "POST",
  });
}

export function closeBrowserSession(sessionId: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/api/browser/sessions/${sessionId}`, {
    method: "DELETE",
  });
}
