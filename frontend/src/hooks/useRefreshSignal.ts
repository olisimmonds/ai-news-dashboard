import { useEffect, useState } from "react";

export function useRefreshSignal(): number {
  const [signal, setSignal] = useState(0);

  useEffect(() => {
    function onRefreshed() {
      setSignal((current) => current + 1);
    }
    window.addEventListener("meridian:refreshed", onRefreshed);
    return () => window.removeEventListener("meridian:refreshed", onRefreshed);
  }, []);

  return signal;
}
