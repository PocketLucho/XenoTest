#!/bin/bash
# Apply the Dreamcast (--target dc) patches to the psxrecomp submodule.
# Usage: dreamcast/apply-psxrecomp-patches.sh   (run from the repository root)
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root/psxrecomp"
for p in "$root"/dreamcast/patches/psxrecomp-*.patch; do
  if git apply --check "$p" 2>/dev/null; then
    git apply "$p"
    echo "applied $(basename "$p")"
  else
    echo "skipped $(basename "$p") (already applied or does not match this psxrecomp commit)"
  fi
done
