# SPDX-License-Identifier: GPL-3.0-only
# shellcheck shell=bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config.env"

die() { echo "error: $*" >&2; exit 1; }

soc_dir()  { echo "$KIT_ROOT/socs/$1"; }
soc_tree() { echo "$KIT_ROOT/worktrees/$1"; }
soc_build(){ echo "$KIT_ROOT/worktrees/$1/build"; }

require_soc() {
  [[ -n "${1:-}" ]] || die "usage: $(basename "$0") <soc> ..."
  [[ -d "$(soc_dir "$1")" ]] || die "no SoC workspace socs/$1 (run scripts/new-soc.sh $1)"
}
