import { useCallback, useEffect, useState } from "react";
import { Outlet } from "react-router-dom";

import { useMeta } from "../hooks/useMeta";
import { Footer } from "./Footer";
import { Header } from "./Header";
import { SearchDialog } from "./SearchDialog";
import { Toast } from "./Toast";

export function Layout() {
  const { meta } = useMeta();
  const [searchOpen, setSearchOpen] = useState(false);
  const openSearch = useCallback(() => setSearchOpen(true), []);
  const closeSearch = useCallback(() => setSearchOpen(false), []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div className="site-shell">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <Header onSearch={openSearch} />
      <main id="main" className="site-main">
        <Outlet />
      </main>
      <Footer
        articleCount={meta?.stats.total_articles ?? 0}
        sourceCount={meta?.stats.enabled_sources ?? 0}
        refreshMinutes={meta?.refresh_interval_minutes ?? 30}
      />
      <Toast />
      <SearchDialog open={searchOpen} onClose={closeSearch} />
    </div>
  );
}
