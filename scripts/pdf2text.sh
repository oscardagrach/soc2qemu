#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Convert a datasheet/TRM PDF into greppable text with page markers ("=== page N ===").
#   scripts/pdf2text.sh <soc> <pdf>   -> socs/<soc>/intake/text/<name>.txt
source "$(dirname "$0")/lib.sh"
soc="${1:-}"; require_soc "$soc"; pdf="${2:-}"; [[ -f "$pdf" ]] || die "no such pdf: $pdf"
out="$(soc_dir "$soc")/intake/text/$(basename "${pdf%.*}").txt"
mkdir -p "$(dirname "$out")"
pdftotext -layout "$pdf" - | awk 'BEGIN{p=1; print "=== page 1 ==="} /\f/{gsub(/\f/,""); p++; print "=== page " p " ==="} {print}' >"$out"
echo "$out ($(grep -c '^=== page' "$out") pages)"
