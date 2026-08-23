import { describe, expect, it } from "vitest";

import { elapsedRunMs, formatRunElapsed } from "./RunTimer";

describe("formatRunElapsed", () => {
  it("formats durations below and above one hour", () => {
    expect(formatRunElapsed(5 * 60 * 1000 + 7 * 1000)).toEqual({
      text: "05:07",
      capped: false,
    });
    expect(formatRunElapsed(60 * 60 * 1000)).toEqual({
      text: "1:00:00",
      capped: false,
    });
    expect(formatRunElapsed(60 * 60 * 1000 + 1)).toEqual({
      text: "1:00:00+",
      capped: true,
    });
  });
});

describe("elapsedRunMs", () => {
  it("uses the finish timestamp when a run is complete", () => {
    expect(
      elapsedRunMs(
        "2026-01-01T00:00:00.000Z",
        "2026-01-01T00:12:34.000Z",
        Date.parse("2026-01-01T01:00:00.000Z"),
      ),
    ).toBe(12 * 60 * 1000 + 34 * 1000);
  });

  it("uses now for an unfinished run and rejects invalid timestamps", () => {
    expect(
      elapsedRunMs(
        "2026-01-01T00:00:00.000Z",
        null,
        Date.parse("2026-01-01T00:01:02.000Z"),
      ),
    ).toBe(62 * 1000);
    expect(elapsedRunMs("not a date", null, Date.now())).toBeNull();
  });
});
