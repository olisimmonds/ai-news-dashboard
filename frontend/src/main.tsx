import "@fontsource-variable/inter";
import "@fontsource-variable/newsreader";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { MetaProvider } from "./context/MetaProvider";
import { ReadingProvider } from "./context/ReadingProvider";
import { ThemeProvider } from "./context/ThemeProvider";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/articles.css";
import "./styles/browser.css";
import "./styles/pages.css";
import "./styles/responsive.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element not found");
}

createRoot(container).render(
  <StrictMode>
    <ThemeProvider>
      <ReadingProvider>
        <MetaProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </MetaProvider>
      </ReadingProvider>
    </ThemeProvider>
  </StrictMode>,
);
