import { useEffect, useState } from "react";

interface ArticleThumbProps {
  url: string;
  alt: string;
  className?: string;
  eager?: boolean;
}

export function ArticleThumb({ url, alt, className, eager = false }: ArticleThumbProps) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [url]);

  if (!url || failed) return null;

  return (
    <img
      className={className}
      src={url}
      alt={alt}
      loading={eager ? "eager" : "lazy"}
      decoding="async"
      fetchPriority={eager ? "high" : "auto"}
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}
