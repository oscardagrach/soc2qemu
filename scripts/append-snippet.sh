#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Append text from stdin to a shared file (trace-events, meson.build, Kconfig) under a lock,
# skipping it if the identical block is already present. Used by parallel device agents.
#   scripts/append-snippet.sh <file> <<'X' ... X
source "$(dirname "$0")/lib.sh"
target="${1:-}"; [[ -f "$target" ]] || die "usage: append-snippet.sh <existing-file> < snippet"
snippet="$(cat)"
exec 9>"$KIT_ROOT/.kit-append.lock"
flock 9
if SNIP="$snippet" python3 -c 'import os,sys; sys.exit(0 if os.environ["SNIP"] in open(sys.argv[1]).read() else 1)' "$target"; then
  echo "already present, skipped: $target"
else
  printf '\n%s\n' "$snippet" >>"$target"
  echo "appended to $target"
fi
