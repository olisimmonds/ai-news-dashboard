import { useContext } from "react";

import { ReadingContext } from "../context/reading";

export function useReading() {
  const value = useContext(ReadingContext);
  if (!value) {
    throw new Error("useReading must be used inside ReadingProvider");
  }
  return value;
}
