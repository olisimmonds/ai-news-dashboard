import type { ReactNode } from "react";

import { Inbox } from "./icons";

interface EmptyStateProps {
  eyebrow?: string;
  title: string;
  description: string;
  action?: ReactNode;
}

export function EmptyState({ eyebrow, title, description, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <span className="empty-icon" aria-hidden>
        <Inbox size={26} />
      </span>
      {eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}
      <h2>{title}</h2>
      <p>{description}</p>
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

interface ErrorStateProps {
  title?: string;
  message: string;
  onRetry?: () => void;
}

export function ErrorState({
  title = "The news desk is unavailable",
  message,
  onRetry,
}: ErrorStateProps) {
  return (
    <div className="empty-state error-state" role="alert">
      <span className="eyebrow">Connection problem</span>
      <h2>{title}</h2>
      <p>{message}</p>
      {onRetry ? (
        <div className="empty-action">
          <button type="button" className="button-secondary" onClick={onRetry}>
            Try again
          </button>
        </div>
      ) : null}
    </div>
  );
}
