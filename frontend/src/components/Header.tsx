import { useState } from "react";
import { Link, NavLink } from "react-router-dom";

import { useTheme } from "../hooks/useTheme";
import { Logo, Menu, Moon, Search, Sun, X } from "./icons";

const NAV_ITEMS = [
  { to: "/", label: "Briefing", end: true },
  { to: "/category/markets-energy", label: "Markets & Energy" },
  { to: "/category/ai-technology", label: "AI & Technology" },
  { to: "/category/general", label: "General News" },
  { to: "/news", label: "All News" },
];

function navClass({ isActive }: { isActive: boolean }): string {
  return isActive ? "nav-link is-active" : "nav-link";
}

export function Header({ onSearch }: { onSearch: () => void }) {
  const { theme, toggleTheme } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <header className="site-header">
      <div className="header-inner">
        <Link to="/" className="wordmark" aria-label="Meridian home">
          <Logo className="wordmark-mark" size={28} />
          <span className="wordmark-text">
            <span className="wordmark-name">Meridian</span>
            <span className="wordmark-tagline">AI · Markets · World</span>
          </span>
        </Link>

        <nav
          id="primary-navigation"
          className={`primary-nav${menuOpen ? " is-open" : ""}`}
          aria-label="Primary"
        >
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={navClass}
              onClick={() => setMenuOpen(false)}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="header-actions">
          <button
            type="button"
            className="icon-button"
            onClick={onSearch}
            aria-label="Search all news"
            title="Search all news (Ctrl K)"
          >
            <Search />
          </button>
          <button
            type="button"
            className="icon-button"
            onClick={toggleTheme}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            {theme === "dark" ? <Sun /> : <Moon />}
          </button>
          <button
            type="button"
            className="icon-button menu-toggle"
            onClick={() => setMenuOpen((value) => !value)}
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={menuOpen}
            aria-controls="primary-navigation"
          >
            {menuOpen ? <X /> : <Menu />}
          </button>
        </div>
      </div>
    </header>
  );
}
