import { useEffect } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { Layout } from "./components/Layout";
import { BriefingPage } from "./pages/BriefingPage";
import { BrowserFavouritesPage } from "./pages/BrowserFavouritesPage";
import { BrowserPage } from "./pages/BrowserPage";
import { CategoryPage } from "./pages/CategoryPage";
import { NotFoundPage } from "./pages/NotFoundPage";

function ScrollToTop() {
  const { pathname } = useLocation();

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return null;
}

export function App() {
  return (
    <>
      <ScrollToTop />
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<BriefingPage />} />
          <Route path="category/:categoryId" element={<CategoryPage />} />
          <Route path="browser" element={<BrowserPage />} />
          <Route path="browser/favourites" element={<BrowserFavouritesPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </>
  );
}
