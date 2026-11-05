import { useMeta } from "../hooks/useMeta";
import { FeedPage } from "./FeedPage";

export function AllNewsPage() {
  const { meta } = useMeta();
  const retention = meta?.retention_days ?? 21;

  return (
    <FeedPage
      title="All News"
      eyebrow="The archive"
      description={`Every story Meridian has collected and retained over the last ${retention} days, across all three desks.`}
      showCategoryFilter
      documentTitle="All News"
    />
  );
}
