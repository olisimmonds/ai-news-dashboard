import { CATEGORIES } from "../lib/categories";
import { formatStoryCount } from "../lib/format";
import type { CategoryId, SortOrder, SourceInfo } from "../lib/types";
import { ChevronDown, Search, X } from "./icons";

interface FilterBarProps {
  searchValue: string;
  onSearchChange: (value: string) => void;
  category?: CategoryId;
  showCategoryFilter: boolean;
  onCategoryChange: (value: CategoryId | undefined) => void;
  categoryCounts?: Partial<Record<CategoryId, number>>;
  source: string;
  onSourceChange: (value: string) => void;
  sources: SourceInfo[];
  sort: SortOrder;
  onSortChange: (value: SortOrder) => void;
  hasFilters: boolean;
  onClear: () => void;
  total: number;
  loading: boolean;
  autoFocus?: boolean;
}

export function FilterBar({
  searchValue,
  onSearchChange,
  category,
  showCategoryFilter,
  onCategoryChange,
  categoryCounts,
  source,
  onSourceChange,
  sources,
  sort,
  onSortChange,
  hasFilters,
  onClear,
  total,
  loading,
  autoFocus,
}: FilterBarProps) {
  const enabledSources = sources
    .filter((item) => item.enabled)
    .sort((left, right) => left.name.localeCompare(right.name));

  return (
    <div className="filter-bar">
      <div className="filter-search-row">
        <label className="search-field">
          <span className="sr-only">Search headlines and descriptions</span>
          <Search size={16} className="search-icon" />
          <input
            type="search"
            value={searchValue}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="Search headlines and descriptions"
            autoFocus={autoFocus}
          />
          {searchValue ? (
            <button
              type="button"
              className="search-clear"
              onClick={() => onSearchChange("")}
              aria-label="Clear search"
            >
              <X size={14} />
            </button>
          ) : null}
        </label>

        <div className="filter-selects">
          <label className="select-field">
            <span className="sr-only">Filter by source</span>
            <select value={source} onChange={(event) => onSourceChange(event.target.value)}>
              <option value="">All sources</option>
              {enabledSources.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <ChevronDown size={14} />
          </label>

          <label className="select-field">
            <span className="sr-only">Sort articles</span>
            <select
              value={sort}
              onChange={(event) => onSortChange(event.target.value as SortOrder)}
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
            <ChevronDown size={14} />
          </label>
        </div>
      </div>

      {showCategoryFilter ? (
        <div className="filter-chips" role="group" aria-label="Filter by category">
          <button
            type="button"
            className={`filter-chip${!category ? " is-active" : ""}`}
            onClick={() => onCategoryChange(undefined)}
            aria-pressed={!category}
          >
            All desks
          </button>
          {CATEGORIES.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`filter-chip${category === item.id ? " is-active" : ""} filter-chip-${item.id}`}
              onClick={() => onCategoryChange(item.id)}
              aria-pressed={category === item.id}
            >
              {item.label}
              {categoryCounts?.[item.id] !== undefined ? (
                <span className="chip-count">{categoryCounts[item.id]}</span>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}

      <div className="filter-status">
        <span aria-live="polite">{loading ? "Updating the feed…" : formatStoryCount(total)}</span>
        {hasFilters ? (
          <button type="button" className="clear-filters" onClick={onClear}>
            Clear filters
          </button>
        ) : null}
      </div>
    </div>
  );
}
