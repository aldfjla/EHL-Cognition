#!/usr/bin/env bash
# Download the MuJoCo Menagerie robot model library into vendor/menagerie.
#
#   ./scripts/fetch_menagerie.sh
#
# Library-first model resolution depends on this: a curated model always beats
# one an agent synthesizes, so this runs before the first real pipeline.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${MENAGERIE_DIR:-$ROOT/vendor/menagerie}"
REPO="https://github.com/google-deepmind/mujoco_menagerie.git"

if [ -d "$DEST/.git" ]; then
  echo "==> Updating existing checkout at $DEST"
  git -C "$DEST" pull --ff-only
else
  echo "==> Cloning MuJoCo Menagerie into $DEST"
  mkdir -p "$(dirname "$DEST")"
  # Shallow: the history is large and we only ever read the working tree.
  git clone --depth 1 "$REPO" "$DEST"
fi

echo "==> Models available:"
find "$DEST" -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf '  %f\n' | sort

# TODO(build): build the index.json that simkit.models.menagerie.index() reads,
# so lookups do not walk the tree and the index can be pasted into a prompt.
echo
echo "Next: build the model index with  python -m simkit.cli models --refresh"
