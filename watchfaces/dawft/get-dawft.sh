#!/usr/bin/env bash
# Fetch + build `dawft` (Da Watch Face Tool) — the MoYoung / Da Fit `.bin` face packer the
# builders in ../solar-soc call to assemble the final face binary.
#
# dawft is a SEPARATE, third-party tool by David Atkinson, licensed GPL-2.0-or-later
# (https://github.com/david47k/dawft). It is NOT bundled in this repo (this project is MIT and
# we keep the GPL tool at arm's length). This script clones it here and builds the `dawft` binary
# that ../solar-soc/build_tou.py resolves at ./dawft/dawft (or via $DAWFT / $PATH).
set -euo pipefail
cd "$(dirname "$0")"

REPO="${DAWFT_REPO:-https://github.com/david47k/dawft}"

if [ ! -d src/.git ]; then
  echo "→ cloning $REPO into ./src"
  git clone --depth 1 "$REPO" src
else
  echo "→ ./src already present; pulling"
  git -C src pull --ff-only || true
fi

echo "→ building (make release)"
if make -C src release 2>/dev/null || make -C src release-gcc; then
  cp -f src/dawft ./dawft
  chmod +x ./dawft
  echo "✓ built ./dawft"
  ./dawft || true
else
  echo "✗ build failed — see src/README.md (needs clang or gcc)"; exit 1
fi
