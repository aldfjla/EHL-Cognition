#!/usr/bin/env bash
# Set up the whole stack as plain local processes. No Docker, no uv.
#
#   ./scripts/setup.sh
#
# Creates .venv, installs the three python packages editable, installs UI deps.
# Idempotent: safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV="$ROOT/.venv"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

# ---- python ---------------------------------------------------------------
say "Checking Python"
"$PYTHON" --version
PY_OK=$("$PYTHON" -c 'import sys; print(1 if sys.version_info >= (3,12) else 0)')
[ "$PY_OK" = "1" ] || die "Python 3.12+ required; found $($PYTHON --version)"

if [ ! -d "$VENV" ]; then
  say "Creating venv at .venv"
  "$PYTHON" -m venv "$VENV"
fi

PIP="$VENV/bin/pip"
say "Upgrading pip"
"$PIP" install --quiet --upgrade pip setuptools wheel

# simkit pulls mujoco, which is the one dependency that may not have a wheel
# for a very new interpreter. Install it first and fail loudly rather than
# leaving a half-set-up tree.
say "Installing simkit (mujoco, numpy, imageio-ffmpeg)"
if ! "$PIP" install -e "$ROOT/packages/simkit"; then
  die "simkit install failed.
If the failure is a missing mujoco wheel for $("$PYTHON" --version | tr -d '\n'),
build the venv against Python 3.12 instead:
    PYTHON=python3.12 ./scripts/setup.sh
Do NOT work around this by dropping mujoco — it is the oracle."
fi

say "Installing orchestrator"
"$PIP" install -e "$ROOT/packages/orchestrator"

say "Installing api (+ dev tools)"
"$PIP" install -e "$ROOT/apps/api"
"$PIP" install --quiet pytest pytest-asyncio ruff

say "Verifying imports"
"$VENV/bin/python" - <<'PYCHECK'
import importlib
for mod in (
    "orchestrator.pipeline",
    "orchestrator.schemas",
    "orchestrator.bus",
    "simkit.runner",
    "simkit.scoring",
    "app.main",
):
    importlib.import_module(mod)
    print(f"  ok  {mod}")
import mujoco
print(f"  ok  mujoco {mujoco.__version__}")
PYCHECK

# ---- node -----------------------------------------------------------------
say "Checking Node"
command -v node >/dev/null || die "node not found; Node 20+ required"
node --version

say "Installing UI dependencies"
(cd "$ROOT/apps/ui" && npm install)

# ---- env ------------------------------------------------------------------
if [ ! -f "$ROOT/.env" ]; then
  say "Creating .env from .env.example"
  cp "$ROOT/.env.example" "$ROOT/.env"
  printf '  Fill in DEVIN_API_KEY and GITHUB_TOKEN before running a real pipeline.\n'
fi

mkdir -p "$ROOT/artifacts"

say "Done"
cat <<'NEXT'
Next:
  make menagerie   download the robot model library (a few hundred MB)
  make smoke       prove DEVIN_API_KEY works
  make dev         run the API and dashboard together
NEXT
