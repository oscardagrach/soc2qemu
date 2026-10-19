#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Create a SoC workspace (socs/<soc>) and a QEMU worktree on branch soc/<soc>, then configure it.
#   scripts/new-soc.sh <soc> [--base upstream|libafl] [--no-configure]
# --base libafl starts the port on AFL++'s qemu-libafl-bridge (QEMU_LIBAFL_REF) instead of
# upstream master, so the finished port can be fuzzed with libafl_qemu (LIBAFL_QEMU_DIR).
# The choice is recorded in socs/<soc>/qemu-base.env and cannot be changed later without
# porting the code to the other base.
source "$(dirname "$0")/lib.sh"
usage="usage: new-soc.sh <soc> [--base upstream|libafl] [--no-configure]"
soc="${1:-}"; [[ -n "$soc" ]] || die "$usage"; shift
base=upstream; configure=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) base="${2:-}"; shift 2 ;;
    --no-configure) configure=0; shift ;;
    *) die "$usage" ;;
  esac
done
[[ "$soc" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "SoC name must be lowercase [a-z0-9_-]"
[[ -d "$QEMU_SRC/.git" ]] || "$KIT_ROOT/scripts/setup-qemu.sh"

case "$base" in
  upstream) ref="$QEMU_BASE_REF" ;;
  libafl)
    git -C "$QEMU_SRC" remote get-url libafl >/dev/null 2>&1 ||
      git -C "$QEMU_SRC" remote add libafl "$QEMU_LIBAFL_REMOTE"
    git -C "$QEMU_SRC" fetch --filter=blob:none libafl
    ref="$QEMU_LIBAFL_REF" ;;
  *) die "--base must be upstream or libafl" ;;
esac

ws="$(soc_dir "$soc")"; tree="$(soc_tree "$soc")"
if [[ -f "$ws/qemu-base.env" ]] && ! grep -qx "QEMU_BASE=$base" "$ws/qemu-base.env"; then
  die "socs/$soc already uses $(grep '^QEMU_BASE=' "$ws/qemu-base.env"); refusing to switch to $base"
fi
if [[ ! -d "$ws" ]]; then
  cp -r "$KIT_ROOT/templates/soc-workspace" "$ws"
  sed -i "s/__SOC__/$soc/g" "$ws"/*.md "$ws"/*.yaml "$ws"/spec/*.yaml "$ws"/bringup/*.md
  echo "created $ws"
fi

if [[ ! -d "$tree" ]]; then
  git -C "$QEMU_SRC" worktree add -b "soc/$soc" "$tree" "$ref"
fi
if [[ ! -f "$ws/qemu-base.env" ]]; then
  cat >"$ws/qemu-base.env" <<X
QEMU_BASE=$base
QEMU_BASE_REF=$ref
QEMU_BASE_COMMIT=$(git -C "$tree" rev-parse "$(git -C "$tree" merge-base HEAD "$ref")")
QEMU_VERSION=$(cat "$tree/VERSION")
X
fi

if [[ $configure -eq 1 && ! -f "$tree/build/build.ninja" ]]; then
  mkdir -p "$tree/build"
  # shellcheck disable=SC2086
  (cd "$tree/build" && ../configure --target-list="$QEMU_TARGETS" $QEMU_CONFIGURE_EXTRA)
fi
echo "workspace: $ws"
echo "qemu tree: $tree (branch soc/$soc, base $base, QEMU $(cat "$tree/VERSION"))"
