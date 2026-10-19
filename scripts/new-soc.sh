#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Create a SoC workspace (socs/<soc>) and a QEMU worktree on branch soc/<soc>, then configure it.
#   scripts/new-soc.sh <soc> [--no-configure]
source "$(dirname "$0")/lib.sh"
soc="${1:-}"; [[ -n "$soc" ]] || die "usage: new-soc.sh <soc> [--no-configure]"
[[ "$soc" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "SoC name must be lowercase [a-z0-9_-]"
[[ -d "$QEMU_SRC/.git" ]] || "$KIT_ROOT/scripts/setup-qemu.sh"

ws="$(soc_dir "$soc")"; tree="$(soc_tree "$soc")"
if [[ ! -d "$ws" ]]; then
  cp -r "$KIT_ROOT/templates/soc-workspace" "$ws"
  sed -i "s/__SOC__/$soc/g" "$ws"/*.md "$ws"/*.yaml "$ws"/spec/*.yaml
  echo "created $ws"
fi

if [[ ! -d "$tree" ]]; then
  git -C "$QEMU_SRC" worktree add -b "soc/$soc" "$tree" "$QEMU_BASE_REF"
fi

if [[ "${2:-}" != "--no-configure" && ! -f "$tree/build/build.ninja" ]]; then
  mkdir -p "$tree/build"
  # shellcheck disable=SC2086
  (cd "$tree/build" && ../configure --target-list="$QEMU_TARGETS" $QEMU_CONFIGURE_EXTRA)
fi
echo "workspace: $ws"
echo "qemu tree: $tree (branch soc/$soc)"
