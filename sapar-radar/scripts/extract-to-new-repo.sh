#!/usr/bin/env bash
#
# Lifts sapar-radar/ out of the host repository into a standalone repo,
# keeping only the commits that touched this directory.
#
#   ./scripts/extract-to-new-repo.sh https://github.com/<you>/sapar-radar.git
#
# Create the empty repo on GitHub first (no README, no .gitignore).

set -euo pipefail

REMOTE="${1:-}"
if [[ -z "$REMOTE" ]]; then
    echo "usage: $0 <git-remote-url>" >&2
    echo "example: $0 https://github.com/rosenthala47-ctrl/sapar-radar.git" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PREFIX="$(basename "$PROJECT_DIR")"
HOST_REPO="$(git -C "$PROJECT_DIR" rev-parse --show-toplevel)"
WORKDIR="$(mktemp -d)"

echo "==> host repo:  $HOST_REPO"
echo "==> subtree:    $PREFIX/"
echo "==> new remote: $REMOTE"

cd "$HOST_REPO"
if [[ -n "$(git status --porcelain)" ]]; then
    echo "error: commit or stash your changes first" >&2
    exit 1
fi

# History for this directory only, rewritten so paths sit at the repo root.
BRANCH="$(git subtree split --prefix="$PREFIX" HEAD)"
echo "==> split commit: $BRANCH"

git clone --quiet --no-local --branch "$(git rev-parse --abbrev-ref HEAD)" \
    "$HOST_REPO" "$WORKDIR/repo" 2>/dev/null || {
        # Fall back to an empty repo seeded from the split commit.
        git init --quiet -b main "$WORKDIR/repo"
    }

cd "$WORKDIR/repo"
git fetch --quiet "$HOST_REPO" "$BRANCH"
git checkout --quiet -B main FETCH_HEAD
git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE"

echo
echo "==> ready in $WORKDIR/repo"
git --no-pager log --oneline -5
echo
echo "Push it with:"
echo "    cd $WORKDIR/repo && git push -u origin main"
