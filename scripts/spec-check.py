#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: GPL-3.0-only
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Validate socs/<soc>/spec/soc.yaml before scaffolding.

    scripts/spec-check.py <soc> [--json]

Errors (exit 1): overlapping regions, peripherals with no strategy, reuse models that don't
exist in the SoC's QEMU tree, missing CPU/boot basics. Warnings: missing provenance, low
confidence on boot-critical parts, shared IRQ numbers, unmapped console.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

KIT = Path(__file__).resolve().parent.parent


def as_int(v):
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip():
        return int(v.replace("_", ""), 0)
    return None


def qom_exists(tree: Path, name: str) -> bool:
    if not tree.exists():
        return True  # can't check yet
    pat = f'define TYPE_[A-Z0-9_]* *"{re.escape(name)}"'
    r = subprocess.run(["git", "-C", str(tree), "grep", "-qE", pat, "--", "include", "hw", "target"],
                       capture_output=True)
    return r.returncode == 0


def cpu_exists(tree: Path, cpu: str) -> bool:
    if not tree.exists():
        return True
    r = subprocess.run(["git", "-C", str(tree), "grep", "-qF", f'"{cpu}"', "--", "target/arm"], capture_output=True)
    return r.returncode == 0


def main() -> int:
    soc = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: spec-check.py <soc> [--json]")
    path = KIT / "socs" / soc / "spec" / "soc.yaml"
    spec = yaml.safe_load(path.read_text()) or {}
    tree = KIT / "worktrees" / soc
    errors, warnings = [], []

    if not spec.get("cpus"):
        errors.append("no cpus")
    for c in spec.get("cpus", []):
        if not c.get("type"):
            errors.append(f"cpu entry without type: {c}")
        elif not cpu_exists(tree, c["type"]):
            errors.append(f"cpu type {c['type']!r} not in target/arm (needs a new CPU model or a documented substitute)")
    ic = spec.get("interrupt_controller") or {}
    if not ic.get("type"):
        errors.append("interrupt_controller.type missing")

    regions = []
    for m in spec.get("memory", []):
        b, s = as_int(m.get("base")), as_int(m.get("size"))
        if b is None or not s:
            errors.append(f"memory {m.get('name')}: base/size missing")
            continue
        if m.get("kind") != "reserved":
            regions.append((b, b + s, f"memory:{m.get('name')}"))
    names = set()
    for p in spec.get("peripherals", []):
        n = p.get("name", "?")
        if n in names:
            errors.append(f"duplicate peripheral name {n}")
        names.add(n)
        b, s = as_int(p.get("base")), as_int(p.get("size"))
        if b is None or not s:
            errors.append(f"{n}: base/size missing")
        elif p.get("status", "okay") != "disabled":
            regions.append((b, b + s, n))
        st = p.get("strategy")
        if st not in ("reuse", "new", "stub"):
            errors.append(f"{n}: strategy must be reuse|new|stub (got {st!r})")
        if st == "reuse":
            if not p.get("qemu_model"):
                errors.append(f"{n}: strategy reuse but no qemu_model")
            elif not qom_exists(tree, p["qemu_model"]):
                errors.append(f"{n}: qemu_model {p['qemu_model']!r} not found in QEMU tree")
        if not p.get("src"):
            warnings.append(f"{n}: no src provenance")
        if p.get("boot_critical") and p.get("confidence") == "low":
            warnings.append(f"{n}: boot-critical but low confidence")
        if p.get("boot_critical") and st == "stub":
            warnings.append(f"{n}: boot-critical but only stubbed")

    regions.sort()
    for (a0, a1, an), (b0, b1, bn) in zip(regions, regions[1:]):
        if b0 < a1:
            errors.append(f"overlap: {an} [{a0:#x}-{a1:#x}) and {bn} [{b0:#x}-{b1:#x})")

    seen = {}
    for p in spec.get("peripherals", []):
        for irq in p.get("irqs") or []:
            if irq.get("parent") and "interrupt-controller" not in str(irq.get("parent")) and irq.get("type") is None:
                continue  # routed through a secondary controller (gpio etc.)
            key = (irq.get("type", "spi"), irq.get("num"))
            if key[1] is not None and key in seen and seen[key] != p.get("name"):
                warnings.append(f"irq {key} shared by {seen[key]} and {p.get('name')}")
            seen.setdefault(key, p.get("name"))

    boot = spec.get("boot") or {}
    if not boot.get("entry_mode"):
        errors.append("boot.entry_mode missing")
    if boot.get("console") and boot["console"] not in names:
        warnings.append(f"boot.console {boot['console']!r} is not a peripheral name")
    blocking = [q for q in spec.get("open_questions") or [] if q.get("blocking")]
    for q in blocking:
        warnings.append(f"blocking open question: {q.get('q')}")

    counts = {}
    for p in spec.get("peripherals", []):
        counts[p.get("strategy")] = counts.get(p.get("strategy"), 0) + 1
    result = {"ok": not errors, "errors": errors, "warnings": warnings, "strategy_counts": counts}
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
    else:
        print(f"{path}: {'OK' if not errors else 'FAIL'} {counts}")
        for e in errors:
            print(f"  ERROR {e}")
        for w in warnings:
            print(f"  warn  {w}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
