import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { EmptyState, ErrorState } from "../components/EmptyState";
import { Plus, Trash } from "../components/icons";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { getBrowserFavourites, getBrowserStatus, saveBrowserFavourites } from "../lib/api";
import type { BrowserFavourite, BrowserStatus } from "../lib/types";

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof Error ? cause.message : fallback;
}

export function BrowserFavouritesPage() {
  useDocumentTitle("Browser Favourites");
  const [status, setStatus] = useState<BrowserStatus | null>(null);
  const [favourites, setFavourites] = useState<BrowserFavourite[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    setLoadFailed(false);
    try {
      const next = await getBrowserStatus({ signal });
      if (signal?.aborted) return;
      setStatus(next);
      if (next.authenticated) {
        const savedFavourites = await getBrowserFavourites({ signal });
        if (!signal?.aborted) setFavourites(savedFavourites);
      }
    } catch (cause) {
      if (signal?.aborted) return;
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setLoadFailed(true);
      setError(messageOf(cause, "Could not load your favourites."));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  function update(index: number, field: keyof BrowserFavourite, value: string) {
    setSaved(false);
    setFavourites((current) =>
      current.map((favourite, position) =>
        position === index ? { ...favourite, [field]: value } : favourite,
      ),
    );
  }

  function add() {
    setSaved(false);
    setFavourites((current) => [...current, { title: "", url: "" }]);
  }

  function remove(index: number) {
    setSaved(false);
    setFavourites((current) => current.filter((_, position) => position !== index));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleaned = favourites.map((favourite) => ({
      title: favourite.title.trim(),
      url: favourite.url.trim(),
    }));
    if (cleaned.some((favourite) => !favourite.title || !favourite.url)) {
      setError("Every favourite needs a title and a website address.");
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const result = await saveBrowserFavourites(cleaned);
      setFavourites(result);
      setSaved(true);
    } catch (cause) {
      setError(messageOf(cause, "Could not save your favourites."));
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="page browser-favourites-page">
        <div className="browser-loading" aria-live="polite">
          <span className="eyebrow">Saved places</span>
          <p>Loading your favourites...</p>
        </div>
      </div>
    );
  }

  if (loadFailed || (error && !status)) {
    return (
      <div className="page browser-favourites-page">
        <ErrorState
          message={error ?? "Could not load your favourites."}
          onRetry={() => void load()}
        />
      </div>
    );
  }

  if (!status?.authenticated) {
    return (
      <div className="page browser-favourites-page">
        <EmptyState
          eyebrow="Private browser"
          title="Sign in to edit favourites"
          description="Favourites are stored separately for each browser account."
          action={
            <Link to="/browser" className="button-secondary">
              Go to the private browser
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="page browser-favourites-page">
      <header className="page-header browser-header">
        <div className="page-eyebrow">Private browser</div>
        <div className="browser-heading-row">
          <div>
            <h1 className="page-title">Saved places</h1>
            <p className="page-deck">
              Shortcuts for {status.user}. Addresses without a scheme will use HTTPS.
            </p>
          </div>
          <Link to="/browser" className="text-link">
            Back to the browser
          </Link>
        </div>
      </header>

      {error ? (
        <p className="browser-warning" role="alert">
          {error}
        </p>
      ) : null}
      {saved ? (
        <p className="browser-success" role="status">
          Favourites saved.
        </p>
      ) : null}

      <form className="browser-favourites-form" onSubmit={(event) => void submit(event)}>
        <div className="browser-favourite-list">
          {favourites.map((favourite, index) => (
            <div className="browser-favourite-row" key={index}>
              <label className="browser-field">
                <span>Title</span>
                <input
                  value={favourite.title}
                  onChange={(event) => update(index, "title", event.target.value)}
                  maxLength={80}
                  aria-label={`Favourite ${index + 1} title`}
                />
              </label>
              <label className="browser-field browser-field-url">
                <span>Address</span>
                <input
                  value={favourite.url}
                  onChange={(event) => update(index, "url", event.target.value)}
                  inputMode="url"
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  maxLength={2048}
                  aria-label={`Favourite ${index + 1} address`}
                />
              </label>
              <button
                type="button"
                className="browser-tool browser-tool-danger"
                aria-label={`Remove ${favourite.title || `favourite ${index + 1}`}`}
                title="Remove"
                onClick={() => remove(index)}
              >
                <Trash size={17} />
              </button>
            </div>
          ))}
        </div>

        {favourites.length === 0 ? (
          <p className="browser-empty">No saved places. Add a website below.</p>
        ) : null}

        <div className="browser-favourites-actions">
          <button
            type="button"
            className="button-quiet"
            onClick={add}
            disabled={saving || favourites.length >= 24}
          >
            <Plus size={16} />
            Add favourite
          </button>
          <div className="browser-favourites-save">
            <Link to="/browser" className="text-link">
              Cancel
            </Link>
            <button type="submit" className="button-secondary" disabled={saving}>
              {saving ? "Saving..." : "Save favourites"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
