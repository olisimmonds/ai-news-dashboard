const relativeFormatter = new Intl.RelativeTimeFormat("en-GB", { numeric: "auto" });
const dateFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
});
const dateTimeFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const dayFormatter = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
});
const countFormatter = new Intl.NumberFormat("en-GB");

export function formatRelativeTime(value: string, now = Date.now()): string {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "";
  const seconds = Math.round((timestamp - now) / 1000);
  const absolute = Math.abs(seconds);
  if (absolute < 45) return "Just now";
  if (absolute < 3600) return relativeFormatter.format(Math.round(seconds / 60), "minute");
  if (absolute < 86_400) return relativeFormatter.format(Math.round(seconds / 3600), "hour");
  if (absolute < 604_800) return relativeFormatter.format(Math.round(seconds / 86_400), "day");
  return dateFormatter.format(new Date(timestamp));
}

export function formatAbsoluteDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : dateTimeFormatter.format(date);
}

export function formatDay(value: Date = new Date()): string {
  return dayFormatter.format(value);
}

export function formatCount(value: number): string {
  return countFormatter.format(value);
}

export function formatSourceCount(value: number): string {
  return `${formatCount(value)} ${value === 1 ? "source" : "sources"}`;
}

export function formatStoryCount(value: number): string {
  return `${formatCount(value)} ${value === 1 ? "story" : "stories"}`;
}
