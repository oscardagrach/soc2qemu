#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: GPL-3.0-only
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Boot a SoC under QEMU once and write a machine-readable summary of how far it got.

    scripts/bringup-run.py <soc> [--timeout S] [--qemu PATH] [--label TEXT] [-- extra qemu args]

Reads socs/<soc>/run.yaml, writes socs/<soc>/bringup/runs/NNN/{summary.json,summary.md,
serialN.log,qemu.log,cmdline.txt}. Prints summary.md. Exit status: 0 if the last milestone
was reached, 1 otherwise (2 on setup errors).

Stops early when the final milestone is reached, when QEMU exits, or when no serial output
has appeared for `stall_seconds`. Before stopping it samples every CPU's PC through QMP to
find where the guest is spinning, and disassembles the hottest spot.
"""
import argparse
import collections
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml

KIT = Path(__file__).resolve().parent.parent

UNIMP_RE = re.compile(
    r"^(?P<dev>.+?): unimplemented device (?P<rw>read|write)\s+"
    r"\(size (?P<size>\d+), offset 0x(?P<off>[0-9a-fA-F]+)(?:, value 0x(?P<val>[0-9a-fA-F]+))?\)"
)
HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
PC_RES = [
    re.compile(r"\bPC=([0-9a-fA-F]{8,16})\b"),   # AArch64
    re.compile(r"\bR15=([0-9a-fA-F]{8})\b"),     # AArch32 / M-profile
]


def load_run_config(soc_dir: Path) -> dict:
    cfg_path = soc_dir / "run.yaml"
    if not cfg_path.exists():
        sys.exit(f"error: {cfg_path} missing")
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    if not cfg.get("machine"):
        sys.exit(f"error: {cfg_path} has no 'machine'")
    return cfg


def find_qemu(soc: str, cfg: dict, override: str | None) -> str:
    if override:
        return override
    q = cfg.get("qemu", "auto")
    if q != "auto":
        return os.path.expandvars(q)
    binary = cfg.get("qemu_binary", "qemu-system-aarch64")
    path = KIT / "worktrees" / soc / "build" / binary
    if not path.exists():
        sys.exit(f"error: {path} not built (scripts/build.sh {soc})")
    return str(path)


def next_run_dir(soc_dir: Path) -> Path:
    runs = soc_dir / "bringup" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    nums = [int(p.name) for p in runs.iterdir() if p.name.isdigit()]
    d = runs / f"{(max(nums) + 1) if nums else 1:03d}"
    d.mkdir()
    return d


class Qmp:
    def __init__(self, path: Path, timeout: float = 5.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(str(path))
        self.buf = b""
        self._recv()  # greeting
        self.cmd("qmp_capabilities")

    def _recv(self) -> dict:
        while True:
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                if line.strip():
                    msg = json.loads(line)
                    if "event" not in msg:
                        return msg
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("QMP closed")
            self.buf += chunk

    def cmd(self, name: str, **args) -> dict:
        req = {"execute": name}
        if args:
            req["arguments"] = args
        self.sock.sendall(json.dumps(req).encode() + b"\n")
        return self._recv()

    def hmp(self, line: str) -> str:
        r = self.cmd("human-monitor-command", **{"command-line": line})
        return r.get("return", r.get("error", {}).get("desc", ""))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def connect_qmp(path: Path, deadline: float, proc: subprocess.Popen) -> Qmp | None:
    while time.time() < deadline and proc.poll() is None:
        if path.exists():
            try:
                return Qmp(path)
            except OSError:
                pass
        time.sleep(0.05)
    return None


def sample_pcs(qmp: Qmp, samples: int, interval: float) -> dict:
    """Return {cpu_index: Counter(pc)} from repeated `info registers -a`."""
    hist: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for _ in range(samples):
        out = qmp.hmp("info registers -a")
        cpu = 0
        for line in out.splitlines():
            m = re.match(r"\s*CPU#(\d+)", line)
            if m:
                cpu = int(m.group(1))
            for rx in PC_RES:
                pm = rx.search(line)
                if pm:
                    hist[cpu][int(pm.group(1), 16)] += 1
        time.sleep(interval)
    return hist


def hotspots(hist: dict, window: int = 64) -> list[dict]:
    """Per CPU: the PC window that captures the most samples (a polling loop shows up here)."""
    result = []
    for cpu, counter in sorted(hist.items()):
        total = sum(counter.values())
        if not total:
            continue
        best_lo, best_n = None, -1
        pcs = sorted(counter)
        for lo in pcs:
            n = sum(c for pc, c in counter.items() if lo <= pc < lo + window)
            if n > best_n:
                best_lo, best_n = lo, n
        in_win = sorted(pc for pc in pcs if best_lo <= pc < best_lo + window)
        result.append({
            "cpu": cpu,
            "samples": total,
            "distinct_pcs": len(counter),
            "hot_range": [hex(in_win[0]), hex(in_win[-1])],
            "hot_fraction": round(best_n / total, 2),
            "spinning": best_n / total >= 0.8,
            "top_pcs": [[hex(pc), c] for pc, c in counter.most_common(6)],
        })
    return result


def parse_qemu_log(path: Path) -> dict:
    unimp = collections.OrderedDict()
    errors = collections.OrderedDict()
    tail = collections.deque(maxlen=25)
    other_lines = 0
    if path.exists():
        with open(path, errors="replace") as f:
            lines = (l.rstrip("\n") for l in f)
            for line in lines:
                m = UNIMP_RE.match(line)
                if m:
                    key = (m["dev"], m["rw"], int(m["off"], 16))
                    e = unimp.setdefault(key, {"device": m["dev"], "rw": m["rw"], "offset": hex(int(m["off"], 16)),
                                               "size": int(m["size"]), "count": 0, "values": []})
                    e["count"] += 1
                    if m["val"] and len(e["values"]) < 4 and ("0x" + m["val"]) not in e["values"]:
                        e["values"].append("0x" + m["val"])
                    tail.append(line)
                    continue
                if not line.strip():
                    continue
                norm = HEX_RE.sub("0x?", line)[:200]
                e = errors.setdefault(norm, {"example": line[:300], "count": 0})
                e["count"] += 1
                other_lines += 1
                tail.append(line[:300])
    by_dev = collections.Counter()
    for e in unimp.values():
        by_dev[e["device"]] += e["count"]
    return {
        "unimp_by_device": by_dev.most_common(),
        # first-seen order matters: early accesses are init, late ones are what the guest is stuck on
        "unimp_accesses": list(unimp.values()),
        "unimp_last": list(unimp.values())[-15:],
        "log_messages": sorted(errors.values(), key=lambda e: -e["count"])[:40],
        "log_tail": list(tail),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("soc")
    ap.add_argument("--timeout", type=float)
    ap.add_argument("--qemu")
    ap.add_argument("--label", default="")
    argv_in = sys.argv[1:]
    extra = argv_in[argv_in.index("--") + 1:] if "--" in argv_in else []
    a = ap.parse_args(argv_in[:argv_in.index("--")] if "--" in argv_in else argv_in)

    soc_dir = KIT / "socs" / a.soc
    if not soc_dir.is_dir():
        print(f"error: no workspace {soc_dir}", file=sys.stderr)
        return 2
    cfg = load_run_config(soc_dir)
    qemu = find_qemu(a.soc, cfg, a.qemu)
    run = next_run_dir(soc_dir)

    timeout = a.timeout or float(cfg.get("timeout", 60))
    stall = float(cfg.get("stall_seconds", 15))
    nserial = int(cfg.get("serial_count", 1))
    milestones = [(m["name"], re.compile(m["regex"])) for m in cfg.get("milestones", [])]
    qmp_path = Path(f"/tmp/kit-qmp-{os.getpid()}.sock")  # unix socket paths have a ~108 byte limit

    env_subst = {"SOC_DIR": str(soc_dir), "KIT_ROOT": str(KIT), "RUN_DIR": str(run)}
    def subst(s: str) -> str:
        for k, v in env_subst.items():
            s = s.replace("${" + k + "}", v)
        return s

    argv = [qemu, "-M", cfg["machine"], "-display", "none", "-monitor", "none",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-d", cfg.get("log_flags", "unimp,guest_errors"), "-D", str(run / "qemu.log")]
    for i in range(nserial):
        argv += ["-serial", f"file:{run / f'serial{i}.log'}"]
    argv += [subst(x) for x in cfg.get("args", [])]
    argv += extra
    (run / "cmdline.txt").write_text(shlex.join(argv) + "\n")

    start = time.time()
    proc = subprocess.Popen(argv, stdout=open(run / "stdout.log", "wb"), stderr=subprocess.STDOUT)
    qmp = connect_qmp(qmp_path, start + 10, proc)

    reached: list[dict] = []
    last_size, last_change = 0, start
    outcome = "timeout"
    serial_files = [run / f"serial{i}.log" for i in range(nserial)]

    def serial_text() -> str:
        return "".join(p.read_text(errors="replace") for p in serial_files if p.exists())

    while True:
        time.sleep(0.25)
        now = time.time()
        if proc.poll() is not None:
            outcome = "qemu-exited"
            break
        size = sum(p.stat().st_size for p in serial_files if p.exists())
        if size != last_size:
            last_size, last_change = size, now
            text = serial_text()
            names = {r["name"] for r in reached}
            for name, rx in milestones:
                if name not in names and rx.search(text):
                    reached.append({"name": name, "t": round(now - start, 2)})
            if milestones and len(reached) == len(milestones):
                outcome = "success"
                break
        if now - last_change > stall:
            outcome = "stall"
            break
        if now - start > timeout:
            outcome = "timeout"
            break

    hist, disasm, hot = {}, "", []
    if qmp and proc.poll() is None:
        try:
            if outcome != "success":
                hist = sample_pcs(qmp, samples=int(cfg.get("pc_samples", 30)), interval=0.05)
                hot = hotspots(hist)
                if hot:
                    lo = int(hot[0]["hot_range"][0], 16)
                    disasm = qmp.hmp(f"x/24i {hex(max(lo - 32, 0))}")
            qmp.cmd("quit")
        except (OSError, ConnectionError, json.JSONDecodeError):
            pass
        qmp.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    qmp_path.unlink(missing_ok=True)

    text = serial_text()
    log = parse_qemu_log(run / "qemu.log")
    stdout_tail = (run / "stdout.log").read_text(errors="replace").splitlines()[-20:]
    summary = {
        "soc": a.soc,
        "run": run.name,
        "label": a.label,
        "outcome": outcome,
        "exit_code": proc.returncode,
        "elapsed_s": round(time.time() - start, 2),
        "qemu_log_bytes": (run / "qemu.log").stat().st_size if (run / "qemu.log").exists() else 0,
        "milestones_total": len(milestones),
        "milestones_reached": reached,
        "last_milestone": reached[-1]["name"] if reached else None,
        # compare runs with (len(milestones_reached), serial_bytes): bigger is progress
        "progress": [len(reached), len(text.encode())],
        "serial_bytes": len(text.encode()),
        "serial_tail": text.splitlines()[-40:],
        "stdout_tail": stdout_tail,
        "pc_hotspots": hot,
        "hot_disasm": disasm.splitlines()[:30],
        **log,
    }
    (run / "summary.json").write_text(json.dumps(summary, indent=2))
    md = render_md(summary, run)
    (run / "summary.md").write_text(md)
    print(md)
    return 0 if outcome == "success" else 1


def render_md(s: dict, run: Path) -> str:
    out = [f"# {s['soc']} run {s['run']} {('- ' + s['label']) if s['label'] else ''}".rstrip(),
           f"outcome: **{s['outcome']}** (exit {s['exit_code']}, {s['elapsed_s']}s), "
           f"milestones {len(s['milestones_reached'])}/{s['milestones_total']}"
           f" (last: {s['last_milestone']}), serial {s['serial_bytes']} bytes",
           f"artifacts: {run}"]
    if s["stdout_tail"]:
        out += ["", "## qemu stdout/stderr (tail)", "```", *s["stdout_tail"], "```"]
    out += ["", "## serial (tail)", "```", *s["serial_tail"][-25:], "```"]
    if s["pc_hotspots"]:
        out += ["", "## pc hotspots"]
        for h in s["pc_hotspots"]:
            out.append(f"- cpu{h['cpu']}: {h['hot_range'][0]}..{h['hot_range'][1]} "
                       f"{int(h['hot_fraction'] * 100)}% of {h['samples']} samples"
                       f"{' (SPINNING)' if h['spinning'] else ''}; top {h['top_pcs'][:3]}")
        if s["hot_disasm"]:
            out += ["```", *s["hot_disasm"], "```"]
    if s["unimp_by_device"]:
        out += ["", "## unimplemented devices (by access count)"]
        out += [f"- {d}: {n}" for d, n in s["unimp_by_device"][:20]]
        out += ["", "## last distinct unimplemented accesses (most recent last)"]
        out += [f"- {e['device']} {e['rw']} +{e['offset']} size {e['size']} x{e['count']}"
                f"{(' values ' + ','.join(e['values'])) if e['values'] else ''}" for e in s["unimp_last"]]
    if s["log_messages"]:
        out += ["", "## other qemu log messages (by count)"]
        out += [f"- x{e['count']}: {e['example']}" for e in s["log_messages"][:15]]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
