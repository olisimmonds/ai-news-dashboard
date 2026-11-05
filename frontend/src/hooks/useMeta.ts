import { useContext } from "react";

import { MetaContext } from "../context/meta";

export function useMeta() {
  const value = useContext(MetaContext);
  if (!value) {
    throw new Error("useMeta must be used inside MetaProvider");
  }
  return value;
}
