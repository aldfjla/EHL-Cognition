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

# Build the index.json that simkit.models.menagerie.index() reads, so lookups
# never walk the tree and the summary can be pasted straight into a prompt.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON="$(command -v python3.12 || command -v python3)"
  fi
fi

echo
echo "==> Building model index at $DEST/index.json"
if MENAGERIE_DIR="$DEST" "$PYTHON" -m simkit.cli models list --refresh >/dev/null; then
  COUNT="$("$PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["models"]))' "$DEST/index.json")"
  echo "    indexed $COUNT models"
else
  echo "    could not build the index; simkit is not importable yet." >&2
  echo "    Run scripts/setup.sh, then: python -m simkit.cli models list --refresh" >&2
fi
