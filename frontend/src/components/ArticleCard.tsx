import { useReading } from "../hooks/useReading";
import { CATEGORY_BY_ID } from "../lib/categories";
import { formatAbsoluteDate, formatRelativeTime } from "../lib/format";
import type { Article, CategoryId } from "../lib/types";
import { ArticleThumb } from "./ArticleThumb";
import { CategoryTag } from "./CategoryTag";
import { ArrowUpRight } from "./icons";
import { KindBadge } from "./KindBadge";

export type ArticleVariant = "lead" | "featured" | "standard" | "compact" | "latest";

interface ArticleCardProps {
  article: Article;
  variant?: ArticleVariant;
  index?: number;
  hideCategory?: CategoryId;
  showSummary?: boolean;
}

export function ArticleCard({
  article,
  variant = "standard",
  index,
  hideCategory,
  showSummary,
}: ArticleCardProps) {
  const { readIds, markRead } = useReading();
  const isRead = readIds.has(article.id);
  const categories = article.categories.filter((category) => category !== hideCategory);
  const primary = categories[0] ?? article.primary_category ?? "general";
  const primaryLabel = CATEGORY_BY_ID[primary]?.label ?? primary;
  const summary = showSummary ?? (variant === "lead" || variant === "standard");
  const Heading = variant === "lead" ? "h2" : variant === "featured" ? "h3" : "h4";
  const hasImage = Boolean(article.image_url);
  const publisherCount = article.publisher_count ?? 0;
  const isMajor = article.is_major;
  const showKind = isMajor || article.kind !== "routine";

  function onLinkClick() {
    markRead(article.id);
  }

  return (
    <article
      className={`article-card article-${variant}${hasImage ? " has-image" : ""}${isRead ? " is-read" : ""}`}
      data-category={primary}
      aria-label={`${primaryLabel}: ${article.title}`}
    >
      {variant === "featured" && index !== undefined ? (
        <span className="featured-index" aria-hidden>
          {String(index + 1).padStart(2, "0")}
        </span>
      ) : null}

      {hasImage ? (
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="article-media"
          onClick={onLinkClick}
          tabIndex={-1}
          aria-hidden
        >
          <ArticleThumb
            url={article.image_url}
            alt=""
            eager={variant === "lead"}
            className="article-image"
          />
          {variant === "lead" || variant === "featured" ? (
            <span className="media-credit">{article.source_name}</span>
          ) : null}
        </a>
      ) : null}

      <div className="article-body">
        {(categories.length > 0 || showKind) && variant !== "compact" ? (
          <div className="article-kicker">
            {categories.map((category) => (
              <CategoryTag key={category} id={category} />
            ))}
            {isMajor ? (
              <span className="kind-badge kind-major">Major</span>
            ) : (
              <KindBadge kind={article.kind} />
            )}
          </div>
        ) : null}

        <Heading className="article-title">
          <a
            href={article.url}
            target="_blank"
            rel="noopener noreferrer"
            className="article-title-link"
            onClick={onLinkClick}
          >
            <span>{article.title}</span>
            <ArrowUpRight size={variant === "lead" ? 20 : 15} className="title-arrow" />
          </a>
        </Heading>

        {summary && article.summary ? <p className="article-summary">{article.summary}</p> : null}

        <div className="article-meta">
          {!isRead ? <span className="unread-dot" title="Unread" aria-label="Unread" /> : null}
          <span className="article-source">{article.source_name}</span>
          <span className="meta-sep" aria-hidden>
            ·
          </span>
          <time
            dateTime={article.published_at}
            title={
              article.published_at_estimated
                ? `Approximate publication time: ${formatAbsoluteDate(article.published_at)}`
                : formatAbsoluteDate(article.published_at)
            }
          >
            {formatRelativeTime(article.published_at)}
          </time>
          {publisherCount > 1 ? (
            <span className="story-coverage">{publisherCount} publishers</span>
          ) : null}
          {variant === "compact" && showKind ? (
            isMajor ? (
              <span className="kind-badge kind-major">Major</span>
            ) : (
              <KindBadge kind={article.kind} />
            )
          ) : null}
        </div>
      </div>
    </article>
  );
}
