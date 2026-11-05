import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { ReadingContext } from "./reading";

const READ_KEY = "meridian.read.v1";
const MAX_READ = 3000;

function loadIds(key: string): Set<number> {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((value): value is number => Number.isInteger(value)));
  } catch {
    return new Set();
  }
}

function persist(key: string, values: Set<number>): void {
  try {
    localStorage.setItem(key, JSON.stringify([...values]));
  } catch {
    // Storage is a convenience; failure must not break reading.
  }
}

function addBounded(current: Set<number>, id: number, max: number): Set<number> {
  if (current.has(id)) return current;
  const next = new Set(current);
  next.add(id);
  if (next.size > max) {
    const oldest = next.values().next().value;
    if (oldest !== undefined) next.delete(oldest);
  }
  return next;
}

export function ReadingProvider({ children }: { children: ReactNode }) {
  const [readIds, setReadIds] = useState<Set<number>>(() => loadIds(READ_KEY));

  useEffect(() => {
    persist(READ_KEY, readIds);
  }, [readIds]);

  useEffect(() => {
    try {
      localStorage.removeItem("meridian.saved.v1");
    } catch {
      // Ignore storage failures from private browsing or disabled storage.
    }
  }, []);

  useEffect(() => {
    function onStorage(event: StorageEvent) {
      if (event.key === READ_KEY) setReadIds(loadIds(READ_KEY));
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const markRead = useCallback((id: number) => {
    setReadIds((current) => addBounded(current, id, MAX_READ));
  }, []);

  const value = useMemo(() => ({ readIds, markRead }), [readIds, markRead]);

  return <ReadingContext.Provider value={value}>{children}</ReadingContext.Provider>;
}
