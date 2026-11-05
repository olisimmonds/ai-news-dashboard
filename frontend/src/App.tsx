import { useEffect } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { Layout } from "./components/Layout";
import { AllNewsPage } from "./pages/AllNewsPage";
import { BriefingPage } from "./pages/BriefingPage";
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
          <Route path="news" element={<AllNewsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </>
  );
}
