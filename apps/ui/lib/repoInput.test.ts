import { describe, expect, it } from "vitest";

import { normalizeRepoInput } from "./repoInput";

describe("normalizeRepoInput", () => {
  it("passes the canonical form through", () => {
    expect(normalizeRepoInput("aldfjla/EHL-Cognition")).toBe(
      "aldfjla/EHL-Cognition",
    );
    expect(normalizeRepoInput("  owner/robot.arm-2  ")).toBe(
      "owner/robot.arm-2",
    );
  });

  it("accepts what a browser or git remote hands you", () => {
    for (const input of [
      "https://github.com/owner/name",
      "https://github.com/owner/name/",
      "https://www.github.com/owner/name",
      "http://github.com/owner/name.git",
      "github.com/owner/name",
      "git@github.com:owner/name.git",
      "ssh://git@github.com/owner/name.git",
      "git+https://github.com/owner/name.git",
      "https://github.com/owner/name?tab=readme-ov-file",
      "https://github.com/owner/name#readme",
    ]) {
      expect(normalizeRepoInput(input), input).toBe("owner/name");
    }
  });

  it("accepts deep links into a repository", () => {
    expect(normalizeRepoInput("https://github.com/owner/name/tree/main")).toBe(
      "owner/name",
    );
    expect(
      normalizeRepoInput("https://github.com/owner/name/blob/main/src/ctl.py"),
    ).toBe("owner/name");
    expect(normalizeRepoInput("https://github.com/owner/name/pull/12")).toBe(
      "owner/name",
    );
  });

  it("rejects anything that does not name one repository", () => {
    for (const input of [
      "",
      "   ",
      "owner",
      "https://github.com/owner",
      "https://github.com/orgs/owner/repositories",
      "owner/name/extra",
      "owner/na me",
    ]) {
      expect(normalizeRepoInput(input), input).toBeNull();
    }
  });
});
