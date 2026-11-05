import { Link } from "react-router-dom";

import { ArrowRight } from "./icons";

interface SectionHeadingProps {
  title: string;
  eyebrow?: string;
  to?: string;
  linkLabel?: string;
  count?: number;
}

export function SectionHeading({
  title,
  eyebrow,
  to,
  linkLabel = "View all",
  count,
}: SectionHeadingProps) {
  return (
    <div className="section-heading">
      <div className="section-heading-text">
        {eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}
        <h2 className="section-title">
          {title}
          {typeof count === "number" ? <span className="section-count">{count}</span> : null}
        </h2>
      </div>
      {to ? (
        <Link to={to} className="text-link">
          {linkLabel}
          <ArrowRight size={15} />
        </Link>
      ) : null}
    </div>
  );
}
