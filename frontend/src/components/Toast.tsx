import { useEffect } from "react";

import { useMeta } from "../hooks/useMeta";
import { X } from "./icons";

export function Toast() {
  const { notice, dismissNotice } = useMeta();

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(dismissNotice, 6000);
    return () => window.clearTimeout(timeout);
  }, [notice, dismissNotice]);

  if (!notice) return null;

  return (
    <div className="toast" role="status" aria-live="polite">
      <span className="toast-dot" aria-hidden />
      <span>{notice}</span>
      <button type="button" className="toast-close" onClick={dismissNotice} aria-label="Dismiss">
        <X size={15} />
      </button>
    </div>
  );
}
