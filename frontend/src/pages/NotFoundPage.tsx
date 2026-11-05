import { Link } from "react-router-dom";

import { EmptyState } from "../components/EmptyState";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

export function NotFoundPage() {
  useDocumentTitle("Page not found");
  return (
    <div className="page">
      <EmptyState
        eyebrow="404"
        title="This page is off the map"
        description="The page you were looking for does not exist. Return to the briefing to carry on reading."
        action={
          <Link to="/" className="button-secondary">
            Back to the briefing
          </Link>
        }
      />
    </div>
  );
}
