#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Create or refresh the base QEMU clone that per-SoC worktrees branch from.
source "$(dirname "$0")/lib.sh"

if [[ ! -d "$QEMU_SRC/.git" ]]; then
  git clone --filter=blob:none "$QEMU_REMOTE" "$QEMU_SRC"
else
  git -C "$QEMU_SRC" fetch --prune origin
fi
git -C "$QEMU_SRC" log -1 --format='base: %h %s (%cs)' "$QEMU_BASE_REF"
