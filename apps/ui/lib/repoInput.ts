/**
 * Normalize whatever a user pastes into the connect box into `owner/name`.
 *
 * People paste what their browser or `git remote -v` gave them, not the
 * canonical short form: a full https URL, an SSH remote, a deep link to a file
 * on a branch. All of those name a repository unambiguously, so refusing them
 * would be pedantry rather than validation.
 */

const FULL_NAME = /^[\w.-]+\/[\w.-]+$/;

/** Path segments that follow `owner/name` in GitHub web URLs. */
const SUBPATHS = new Set([
  "actions",
  "blame",
  "blob",
  "branches",
  "commit",
  "commits",
  "compare",
  "discussions",
  "issues",
  "pull",
  "pulls",
  "raw",
  "releases",
  "settings",
  "tree",
  "wiki",
]);

/**
 * Return `owner/name`, or `null` when the input does not name a repository.
 */
export function normalizeRepoInput(raw: string): string | null {
  let value = raw.trim();
  if (value === "") return null;

  value = value.replace(/^git\+/, "");
  value = value.replace(/^[a-z][a-z0-9+.-]*:\/\//i, ""); // https://, ssh://, git://
  value = value.replace(/^[^@/\s]+@/, ""); // git@github.com:owner/name
  value = value.replace(/^github\.com[:/]/i, "");
  value = value.replace(/^www\.github\.com[:/]/i, "");
  value = value.replace(/[?#].*$/, "");
  value = value.replace(/\.git$/i, "");
  value = value.replace(/^\/+/, "").replace(/\/+$/, "");

  const segments = value.split("/").filter((segment) => segment !== "");
  if (segments.length < 2) return null;

  const [owner, name, next] = segments;
  if (segments.length > 2 && !SUBPATHS.has(next.toLowerCase())) return null;

  const candidate = `${owner}/${name}`;
  return FULL_NAME.test(candidate) ? candidate : null;
}
