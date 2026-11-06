import { useEffect, useRef, useState, type FormEvent } from "react";

import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useReading } from "../hooks/useReading";
import { getArticles } from "../lib/api";
import { formatRelativeTime } from "../lib/format";
import type { Article } from "../lib/types";
import { ArticleThumb } from "./ArticleThumb";
import { Search, X } from "./icons";

interface SearchDialogProps {
  open: boolean;
  onClose: () => void;
}

export function SearchDialog({ open, onClose }: SearchDialogProps) {
  const { markRead } = useReading();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query, 250);
  const [results, setResults] = useState<Article[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setResults([]);
    setError(null);
    const focusTimer = window.setTimeout(() => inputRef.current?.focus(), 20);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.clearTimeout(focusTimer);
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    const term = debounced.trim();
    if (!open || term.length < 2) {
      setResults([]);
      setLoading(false);
      setError(null);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getArticles({ q: term, limit: 6 }, { signal: controller.signal })
      .then((page) => {
        if (controller.signal.aborted) return;
        setResults(page.items);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "Search failed.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [debounced, open]);

  if (!open) return null;

  const term = query.trim();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
  }

  return (
    <div
      className="search-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="search-dialog" role="dialog" aria-modal="true" aria-label="Search news">
        <form className="search-dialog-form" onSubmit={submit}>
          <Search size={19} className="search-icon" />
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search headlines and descriptions"
            aria-label="Search headlines and descriptions"
            autoComplete="off"
          />
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close search">
            <X size={18} />
          </button>
        </form>

        <div className="search-dialog-body" aria-live="polite">
          {term.length < 2 ? (
            <p className="search-hint">Type at least two characters to search the archive.</p>
          ) : null}

          {loading && results.length === 0 ? (
            <p className="search-hint">Searching the desk.</p>
          ) : null}

          {error ? <p className="search-hint search-error">{error}</p> : null}

          {!loading && term.length >= 2 && !error && results.length === 0 ? (
            <p className="search-hint">No stories match “{term}”.</p>
          ) : null}

          {results.length > 0 ? (
            <>
              <div className="search-results">
                {results.map((article) => (
                  <a
                    key={article.id}
                    href={article.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="search-result"
                    onClick={() => {
                      markRead(article.id);
                      onClose();
                    }}
                  >
                    {article.image_url ? (
                      <span className="search-result-media">
                        <ArticleThumb
                          url={article.image_url}
                          alt=""
                          className="search-result-image"
                        />
                      </span>
                    ) : null}
                    <span className="search-result-text">
                      <span className="search-result-title">{article.title}</span>
                      <span className="search-result-meta">
                        {article.source_name}
                        <span aria-hidden> · </span>
                        {formatRelativeTime(article.published_at)}
                      </span>
                    </span>
                  </a>
                ))}
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
