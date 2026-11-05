import { useEffect } from "react";

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} — Meridian` : "Meridian — AI, markets & the world";
  }, [title]);
}
