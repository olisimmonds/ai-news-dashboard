import { useCallback, useEffect, useRef, useState } from "react";

import { getArticles } from "../lib/api";
import type { Article, CategoryId, SortOrder } from "../lib/types";
import { useRefreshSignal } from "./useRefreshSignal";

export interface FeedFilters {
  category?: CategoryId;
  source?: string;
  q?: string;
  sort?: SortOrder;
  limit?: number;
}

function messageFor(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Could not load articles.";
}

export function useArticleFeed(filters: FeedFilters) {
  const { category, source, q, sort, limit = 30 } = filters;
  const refreshSignal = useRefreshSignal();
  const [items, setItems] = useState<Article[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
    const currentGeneration = generation.current;
    const controller = new AbortController();

    setLoading(true);
    setError(null);

    getArticles(
      { category, source, q: q || undefined, sort, limit, offset: 0 },
      { signal: controller.signal },
    )
      .then((page) => {
        if (currentGeneration !== generation.current || controller.signal.aborted) return;
        setItems(page.items);
        setTotal(page.total);
        setHasMore(page.has_more);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(messageFor(cause));
      })
      .finally(() => {
        if (currentGeneration === generation.current && !controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [category, source, q, sort, limit, refreshSignal, retryKey]);

  const loadMore = useCallback(async () => {
    if (loading || loadingMore || !hasMore) return;
    const currentGeneration = generation.current;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await getArticles({
        category,
        source,
        q: q || undefined,
        sort,
        limit,
        offset: items.length,
      });
      if (currentGeneration !== generation.current) return;
      setItems((current) => {
        const seen = new Set(current.map((article) => article.id));
        const additions = page.items.filter((article) => !seen.has(article.id));
        return [...current, ...additions];
      });
      setTotal(page.total);
      setHasMore(page.has_more);
    } catch (cause) {
      if (currentGeneration === generation.current) {
        setError(messageFor(cause));
      }
    } finally {
      if (currentGeneration === generation.current) {
        setLoadingMore(false);
      }
    }
  }, [category, source, q, sort, limit, items.length, loading, loadingMore, hasMore]);

  const retry = useCallback(() => setRetryKey((value) => value + 1), []);

  return {
    items,
    total,
    hasMore,
    loading,
    loadingMore,
    error,
    loadMore,
    retry,
  };
}
