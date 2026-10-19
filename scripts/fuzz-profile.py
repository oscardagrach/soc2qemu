#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: GPL-3.0-only
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Export what a fuzzing harness needs to know about a ported SoC.

    scripts/fuzz-profile.py <soc> [--last N] [-o out.yaml]

Combines spec/soc.yaml, run.yaml, qemu-base.env and the bring-up runs (all of them, or the
last N) into socs/<soc>/fuzz/profile.yaml (schema soc2qemu-fuzz-profile/1):

- qemu: which tree/commit the port lives on (LIBAFL_QEMU_DIR for libafl_qemu) and how it boots
- memory / mmio: the address map
- input_candidates: MMIO reads no model answered, resolved to absolute addresses; `polled`
  marks the ones read in a loop (good places to feed fuzz input)
- snapshot_candidates: boot milestones reached and the PCs where firmware waited
- baseline_log_messages: QEMU log noise seen in normal boots, to keep out of a crash oracle
- targets: left empty here; start_pc / end_pc / input buffer come from firmware analysis

The fuzzer itself is out of scope for this kit; this file is the hand-off.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

KIT = Path(__file__).resolve().parent.parent
SCHEMA = "soc2qemu-fuzz-profile/1"
POLL_THRESHOLD = 1000  # reads of one register in one run before we call it polled


def as_int(v):
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip():
        return int(v.replace("_", ""), 0)
    return None


def hexify(v):
    """Addresses and sizes as hex strings; everything else unchanged."""
    return hex(v) if isinstance(v, int) and not isinstance(v, bool) else v


def read_env(path: Path) -> dict:
    if not path.exists():
        return {"QEMU_BASE": "upstream"}
    return dict(l.split("=", 1) for l in path.read_text().splitlines() if "=" in l and not l.startswith("#"))


def resolve(device: str, soc: str, periph: dict) -> dict | None:
    """Map an unimplemented-device log name back to a spec peripheral."""
    for cand in (device, device.removeprefix(f"{soc}."), device.split(".")[-1]):
        if cand in periph:
            return periph[cand]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("soc")
    ap.add_argument("--last", type=int, help="only use the last N runs")
    ap.add_argument("-o", dest="out", type=Path)
    a = ap.parse_args()

    ws = KIT / "socs" / a.soc
    spec = yaml.safe_load((ws / "spec" / "soc.yaml").read_text()) or {}
    run_cfg = yaml.safe_load((ws / "run.yaml").read_text()) or {}
    base = read_env(ws / "qemu-base.env")

    runs = sorted((ws / "bringup" / "runs").glob("[0-9]*/summary.json"))
    if a.last:
        runs = runs[-a.last:]
    summaries = [json.loads(p.read_text()) for p in runs]
    if not summaries:
        print(f"warning: no bring-up runs in {ws}/bringup/runs; profile will lack runtime data", file=sys.stderr)

    periph = {p["name"]: p for p in spec.get("peripherals", []) if p.get("name")}

    inputs: dict[tuple, dict] = {}
    for s in summaries:
        for e in s.get("unimp_accesses", []):
            if e["rw"] != "read":
                continue
            key = (e["device"], e["offset"])
            p = resolve(e["device"], a.soc, periph)
            off = int(e["offset"], 16)
            c = inputs.setdefault(key, {
                "device": e["device"],
                "peripheral": p["name"] if p else None,
                "offset": e["offset"],
                "address": hex(as_int(p["base"]) + off) if p and as_int(p.get("base")) is not None else None,
                "size": e["size"],
                "max_reads_per_run": 0,
                "polled": False,
                "runs": [],
            })
            c["max_reads_per_run"] = max(c["max_reads_per_run"], e["count"])
            c["polled"] = c["polled"] or e["count"] >= POLL_THRESHOLD
            c["runs"].append(s["run"])
    input_list = sorted(inputs.values(), key=lambda c: (not c["polled"], -c["max_reads_per_run"]))

    reached: dict[str, dict] = {}
    for s in summaries:
        for m in s.get("milestones_reached", []):
            r = reached.setdefault(m["name"], {"runs": [], "best_time_s": m["t"]})
            r["runs"].append(s["run"])
            r["best_time_s"] = min(r["best_time_s"], m["t"])
    milestones = [{"name": m["name"], "regex": m["regex"], **({"reached": reached[m["name"]]} if m["name"] in reached else {})}
                  for m in run_cfg.get("milestones", [])]

    hot = [{"run": s["run"], "cpu": h["cpu"], "range": h["hot_range"], "spinning": h["spinning"],
            "fraction": h["hot_fraction"], "last_milestone": s.get("last_milestone")}
           for s in summaries for h in s.get("pc_hotspots", [])]

    noise: dict[str, dict] = {}
    for s in summaries:
        for m in s.get("log_messages", []):
            key = re.sub(r"0x[0-9a-fA-F]+", "0x?", m["example"])
            noise.setdefault(key, {"example": m["example"], "runs": 0})["runs"] += 1

    cmdline = (runs[-1].parent / "cmdline.txt").read_text().strip() if runs else None
    profile = {
        "schema": SCHEMA,
        "soc": a.soc,
        "profile": (spec.get("soc") or {}).get("profile"),
        "cpus": spec.get("cpus", []),
        "interrupt_controller": (spec.get("interrupt_controller") or {}).get("type"),
        "qemu": {
            "base": base.get("QEMU_BASE"),
            "base_ref": base.get("QEMU_BASE_REF"),
            "base_commit": base.get("QEMU_BASE_COMMIT"),
            "version": base.get("QEMU_VERSION"),
            "tree": str(KIT / "worktrees" / a.soc),
            "branch": f"soc/{a.soc}",
            "libafl_compatible": base.get("QEMU_BASE") == "libafl",
            "binary": run_cfg.get("qemu_binary", "qemu-system-aarch64"),
            "machine": run_cfg.get("machine"),
            "args": run_cfg.get("args", []),
            "last_cmdline": cmdline,
        },
        "boot": spec.get("boot", {}),
        "memory": [{k: hexify(m.get(k)) for k in ("name", "kind", "base", "size")} for m in spec.get("memory", [])],
        "mmio": [{k: hexify(p.get(k)) for k in ("name", "class", "base", "size", "strategy", "qemu_model", "boot_critical")}
                 for p in spec.get("peripherals", [])],
        "input_candidates": input_list,
        "snapshot_candidates": {"milestones": milestones, "hot_pcs": hot},
        "baseline_log_messages": list(noise.values()),
        "targets": {"start_pc": None, "end_pc": None, "input_buffer": None, "input_max_len": None,
                    "crash_pcs": [], "notes": "fill from firmware analysis (qemu-firmware-re)"},
        "provenance": {"runs": [s["run"] for s in summaries], "spec": "spec/soc.yaml", "run_config": "run.yaml"},
    }

    out = a.out or ws / "fuzz" / "profile.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(profile, sort_keys=False, width=120))
    print(f"{out}: base {profile['qemu']['base']} (QEMU {profile['qemu']['version']}), "
          f"{len(summaries)} runs, {len(input_list)} input candidates "
          f"({sum(c['polled'] for c in input_list)} polled), {len(hot)} hot PCs")
    if not profile["qemu"]["libafl_compatible"]:
        print("note: port is on upstream QEMU; libafl_qemu needs a port based on qemu-libafl-bridge "
              "(scripts/new-soc.sh <soc> --base libafl)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
