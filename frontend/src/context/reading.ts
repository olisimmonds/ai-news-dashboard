import { createContext } from "react";

export interface ReadingContextValue {
  readIds: ReadonlySet<number>;
  markRead: (id: number) => void;
}

export const ReadingContext = createContext<ReadingContextValue | null>(null);
