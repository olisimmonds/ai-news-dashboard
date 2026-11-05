import { describe, expect, it } from "vitest";

import {
  formatAbsoluteDate,
  formatCount,
  formatRelativeTime,
  formatSourceCount,
  formatStoryCount,
} from "./format";

const NOW = new Date("2026-11-03T09:00:00.000Z").getTime();

describe("formatRelativeTime", () => {
  it("returns just now for very recent timestamps", () => {
    expect(formatRelativeTime(new Date(NOW - 20_000).toISOString(), NOW)).toBe("Just now");
  });

  it("formats minutes, hours and days", () => {
    expect(formatRelativeTime(new Date(NOW - 5 * 60_000).toISOString(), NOW)).toBe("5 minutes ago");
    expect(formatRelativeTime(new Date(NOW - 3 * 3_600_000).toISOString(), NOW)).toBe(
      "3 hours ago",
    );
    expect(formatRelativeTime(new Date(NOW - 2 * 86_400_000).toISOString(), NOW)).toBe(
      "2 days ago",
    );
  });

  it("falls back to an absolute date for old stories", () => {
    expect(formatRelativeTime("2026-09-01T09:00:00.000Z", NOW)).toBe("1 Sept 2026");
  });

  it("returns an empty string for invalid timestamps", () => {
    expect(formatRelativeTime("not-a-date", NOW)).toBe("");
    expect(formatAbsoluteDate("not-a-date")).toBe("");
  });
});

describe("formatCount helpers", () => {
  it("pluralises counts", () => {
    expect(formatSourceCount(1)).toBe("1 source");
    expect(formatSourceCount(50)).toBe("50 sources");
    expect(formatStoryCount(1)).toBe("1 story");
    expect(formatStoryCount(42)).toBe("42 stories");
    expect(formatCount(1234)).toBe("1,234");
  });
});
