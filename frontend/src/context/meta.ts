import { createContext } from "react";

import type { Meta } from "../lib/types";

export interface MetaContextValue {
  meta: Meta | null;
  loading: boolean;
  error: string | null;
  refreshing: boolean;
  notice: string | null;
  reload: () => Promise<void>;
  refreshNews: () => Promise<void>;
  dismissNotice: () => void;
}

export const MetaContext = createContext<MetaContextValue | null>(null);
