import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { getMeta, triggerRefresh } from "../lib/api";
import type { Meta } from "../lib/types";
import { MetaContext } from "./meta";

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export function MetaProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const lastLoaded = useRef(0);
  const lastRefreshId = useRef<number | null>(null);

  const loadMeta = useCallback(async (silent = false) => {
    try {
      const next = await getMeta();
      setMeta(next);
      setError(null);
      lastLoaded.current = Date.now();
    } catch (cause) {
      if (!silent) setError(errorMessage(cause));
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMeta();
    const metaPoll = window.setInterval(() => void loadMeta(true), 5 * 60 * 1000);
    return () => window.clearInterval(metaPoll);
  }, [loadMeta]);

  useEffect(() => {
    function onVisibilityChange() {
      if (document.visibilityState === "visible" && Date.now() - lastLoaded.current > 120_000) {
        void loadMeta(true);
      }
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [loadMeta]);

  useEffect(() => {
    if (!meta?.is_refreshing) return;
    const poll = window.setInterval(() => void loadMeta(true), 2500);
    return () => window.clearInterval(poll);
  }, [meta?.is_refreshing, loadMeta]);

  useEffect(() => {
    const id = meta?.stats.last_refresh?.id ?? null;
    if (lastRefreshId.current !== null && id !== null && id !== lastRefreshId.current) {
      window.dispatchEvent(new CustomEvent("meridian:refreshed"));
    }
    lastRefreshId.current = id;
  }, [meta]);

  const refreshNews = useCallback(async () => {
    if (refreshing || meta?.is_refreshing) return;
    setRefreshing(true);
    setNotice("Updating the news desk…");
    setError(null);
    try {
      await triggerRefresh();
      let finished = false;
      for (let attempt = 0; attempt < 90; attempt += 1) {
        await sleep(2000);
        const next = await getMeta();
        setMeta(next);
        if (!next.is_refreshing) {
          finished = true;
          const status = next.stats.last_refresh?.status;
          if (status === "partial" || status === "failed") {
            setNotice("News updated, but some sources could not be reached.");
          } else {
            setNotice("The news desk is up to date.");
          }
          break;
        }
      }
      if (!finished) {
        setNotice("The update is taking longer than expected. It will finish in the background.");
      }
    } catch (cause) {
      setNotice(errorMessage(cause));
    } finally {
      setRefreshing(false);
      await loadMeta(true);
    }
  }, [loadMeta, meta?.is_refreshing, refreshing]);

  const dismissNotice = useCallback(() => setNotice(null), []);

  const value = useMemo(
    () => ({
      meta,
      loading,
      error,
      refreshing: refreshing || (meta?.is_refreshing ?? false),
      notice,
      reload: () => loadMeta(),
      refreshNews,
      dismissNotice,
    }),
    [meta, loading, error, refreshing, notice, loadMeta, refreshNews, dismissNotice],
  );

  return <MetaContext.Provider value={value}>{children}</MetaContext.Provider>;
}
