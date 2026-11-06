import { Link } from "react-router-dom";

import { formatSourceCount, formatStoryCount } from "../lib/format";

interface FooterProps {
  articleCount: number;
  sourceCount: number;
  refreshMinutes: number;
}

export function Footer({ articleCount, sourceCount, refreshMinutes }: FooterProps) {
  return (
    <footer className="site-footer">
      <div className="footer-inner">
        <div className="footer-brand">
          <span className="footer-name">Meridian</span>
          <p>
            A quiet place for markets, AI and the world. Built to run on a Raspberry Pi and read
            from a phone.
          </p>
        </div>
        <div className="footer-links">
          <Link to="/">Briefing</Link>
          <Link to="/browser">Private browser</Link>
          <a
            href="https://github.com/olisimmonds/ai-news-dashboard"
            target="_blank"
            rel="noopener noreferrer"
          >
            GitHub
          </a>
        </div>
        <div className="footer-meta">
          <span>{formatStoryCount(articleCount)}</span>
          <span>{formatSourceCount(sourceCount)}</span>
          <span>Updated every {refreshMinutes} minutes</span>
        </div>
      </div>
    </footer>
  );
}
