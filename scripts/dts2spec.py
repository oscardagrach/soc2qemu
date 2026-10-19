#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: GPL-3.0-only
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Turn a devicetree (DTS or DTB) into a draft partial spec for intake.

    scripts/dts2spec.py <file.dts|file.dtb> [-I include_dir ...] [-o out.yaml] [--src-id NAME]

Kernel-style DTS files with #include are run through cpp first (pass -I for the kernel's
include/ and arch/arm64/boot/dts/<vendor>/). Everything is normalized through dtc, so labels,
macros and overlays are resolved before parsing. Output follows spec/soc.yaml's shape:
cpus, interrupt_controller, memory, peripherals (with absolute addresses after `ranges`
translation, GIC SPI numbers, clock providers, and a reuse suggestion from
reference/compat-map.yaml). Facts carry src: dts:<file>:<node path>.
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

KIT = Path(__file__).resolve().parent.parent


# ---------- normalize via cpp + dtc ----------

def to_normalized_dts(path: Path, includes: list[str]) -> str:
    with tempfile.TemporaryDirectory() as td:
        if path.suffix in (".dtb", ".dtbo") or path.read_bytes()[:4] == b"\xd0\x0d\xfe\xed":
            dtb = path
        else:
            pre = Path(td) / "pre.dts"
            inc = [f"-I{d}" for d in [str(path.parent), *includes]]
            subprocess.run(["cpp", "-nostdinc", "-undef", "-x", "assembler-with-cpp", "-P", *inc,
                            "-D__DTS__", "-o", str(pre), str(path)], check=True)
            dtb = Path(td) / "out.dtb"
            dtc_inc = [a for d in [str(path.parent), *includes] for a in ("-i", d)]
            subprocess.run(["dtc", "-q", "-I", "dts", "-O", "dtb", *dtc_inc, "-o", str(dtb), str(pre)], check=True)
        return subprocess.run(["dtc", "-q", "-I", "dtb", "-O", "dts", str(dtb)],
                              check=True, capture_output=True, text=True).stdout


# ---------- tiny parser for dtc's normalized output ----------

TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|<[^>]*>|\[[^\]]*\]|[{};=,]|[^\s{};=,]+')


class Node:
    def __init__(self, name: str, parent: "Node | None"):
        self.name, self.parent = name, parent
        self.props: dict[str, list] = {}
        self.children: list[Node] = []

    @property
    def path(self) -> str:
        if self.parent is None:
            return "/"
        return (self.parent.path.rstrip("/") + "/" + self.name)

    def cells(self, prop: str) -> list[int]:
        return [v for part in self.props.get(prop, []) if isinstance(part, list) for v in part]

    def strings(self, prop: str) -> list[str]:
        return [p for p in self.props.get(prop, []) if isinstance(p, str)]

    def u32(self, prop: str, default=None):
        c = self.cells(prop)
        return c[0] if c else default

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def parse(text: str) -> Node:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"^\s*(/dts-v1/|/memreserve/[^;]*|/plugin/);", "", text, flags=re.M)
    toks = TOKEN.findall(text)
    root, cur, i = None, None, 0
    while i < len(toks):
        t = toks[i]
        if i + 1 < len(toks) and toks[i + 1] == "{":
            name = t.split(":")[-1]
            n = Node(name if cur else "/", cur)
            if cur:
                cur.children.append(n)
            else:
                root = n
            cur, i = n, i + 2
            continue
        if t == "}":
            cur = cur.parent if cur.parent else cur
            i += 2 if i + 1 < len(toks) and toks[i + 1] == ";" else 1
            continue
        if t == ";":
            i += 1
            continue
        name, i = t, i + 1
        values = []
        if i < len(toks) and toks[i] == "=":
            i += 1
            while toks[i] != ";":
                v = toks[i]
                if v.startswith('"'):
                    values.append(bytes(v[1:-1], "utf-8").decode("unicode_escape"))
                elif v.startswith("<"):
                    values.append([int(x, 0) for x in v[1:-1].split() if not x.startswith("&")])
                elif v.startswith("["):
                    values.append(bytes.fromhex(v[1:-1].replace(" ", "")))
                i += 1
        cur.props[name] = values
        i += 1
    return root


# ---------- interpretation ----------

def addr_cells(n: Node | None) -> int:
    return n.u32("#address-cells", 2) if n else 2


def size_cells(n: Node | None) -> int:
    return n.u32("#size-cells", 1) if n else 1


def join(cells: list[int]) -> int:
    v = 0
    for c in cells:
        v = (v << 32) | c
    return v


def translate(node: Node, addr: int) -> int | None:
    """Map a child-bus address up to the CPU address space through each ancestor's `ranges`."""
    bus = node.parent
    while bus is not None and bus.parent is not None:
        if "ranges" not in bus.props:
            return None  # no ranges: not memory-mapped from the CPU's point of view
        r = bus.cells("ranges")
        if r:
            ca, pa, sz = addr_cells(bus), addr_cells(bus.parent), size_cells(bus)
            step = ca + pa + sz
            for k in range(0, len(r) - step + 1, step):
                cbase, pbase, size = join(r[k:k + ca]), join(r[k + ca:k + ca + pa]), join(r[k + ca + pa:k + step])
                if cbase <= addr < cbase + size:
                    addr = pbase + (addr - cbase)
                    break
            else:
                return None
        bus = bus.parent
    return addr


def regs(node: Node) -> list[tuple[int, int]]:
    r = node.cells("reg")
    ac, sc = addr_cells(node.parent), size_cells(node.parent)
    out = []
    if ac == 0 or not r:
        return out
    for k in range(0, len(r) - (ac + sc) + 1, ac + sc):
        a = translate(node, join(r[k:k + ac]))
        if a is not None:
            out.append((a, join(r[k + ac:k + ac + sc])))
    return out


def irq_parent(node: Node, by_phandle: dict) -> Node | None:
    n = node
    while n is not None:
        ph = n.u32("interrupt-parent")
        if ph is not None:
            return by_phandle.get(ph)
        n = n.parent
    return None


def decode_irqs(node: Node, by_phandle: dict) -> list[dict]:
    out = []
    specs: list[tuple[Node | None, list[int]]] = []
    if "interrupts-extended" in node.props:
        c = node.cells("interrupts-extended")
        k = 0
        while k < len(c):
            p = by_phandle.get(c[k])
            nc = p.u32("#interrupt-cells", 1) if p else 1
            specs.append((p, c[k + 1:k + 1 + nc]))
            k += 1 + nc
    elif "interrupts" in node.props:
        p = irq_parent(node, by_phandle)
        nc = p.u32("#interrupt-cells", 1) if p else 1
        c = node.cells("interrupts")
        specs += [(p, c[k:k + nc]) for k in range(0, len(c), nc)]
    names = node.strings("interrupt-names")
    for idx, (p, spec) in enumerate(specs):
        e = {"line": idx, "parent": p.name if p else None, "spec": [hex(x) for x in spec]}
        compat = " ".join(p.strings("compatible")) if p else ""
        if "gic" in compat and len(spec) >= 3:
            e["type"] = {0: "spi", 1: "ppi"}.get(spec[0], f"type{spec[0]}")
            e["num"] = spec[1]
            e["trigger"] = "edge" if spec[2] & 0x3 else "level"
        elif len(spec) >= 1:
            e["num"] = spec[0]
        if idx < len(names):
            e["name"] = names[idx]
        out.append(e)
    return out


def decode_clocks(node: Node, by_phandle: dict) -> list[dict]:
    c, out, k = node.cells("clocks"), [], 0
    names = node.strings("clock-names")
    while k < len(c):
        p = by_phandle.get(c[k])
        nc = p.u32("#clock-cells", 0) if p else 0
        e = {"provider": p.name if p else hex(c[k]), "spec": c[k + 1:k + 1 + nc]}
        if p and "clock-frequency" in p.props:
            e["hz"] = p.u32("clock-frequency")
        if len(out) < len(names):
            e["name"] = names[len(out)]
        out.append(e)
        k += 1 + nc
    return out


CLASS_HINTS = [
    ("uart", r"uart|serial|usart|16550"), ("timer", r"timer|ttc|sp804"), ("wdt", r"wdt|watchdog"),
    ("gpio", r"gpio|pinctrl-gpio"), ("i2c", r"i2c"), ("qspi", r"qspi|ospi|xspi|fspi|spi-nor-ctrl"),
    ("spi", r"spi"), ("mmc", r"mmc|sdhci|sdio|emmc"), ("eth", r"ether|gem|macb|gmac|stmmac|fec\b"),
    ("usb", r"usb|dwc[23]|ehci|ohci|xhci"), ("dma", r"dma|pl330|pl080"), ("clock", r"\bclk|clock|cru|ccm|pll"),
    ("reset", r"reset|rst"), ("pinctrl", r"pinctrl|pinmux|iomux"), ("sysctl", r"syscon|sysctrl|scu\b|grf"),
    ("ddr", r"ddr|dmc|dram|lpddr"), ("rtc", r"rtc"), ("rng", r"rng|trng"), ("crypto", r"crypto|aes|sha"),
    ("efuse", r"efuse|otp|nvmem"), ("mailbox", r"mailbox|mbox|ipc"), ("pcie", r"pcie"), ("iommu", r"smmu|iommu"),
]


def guess_class(node: Node) -> str:
    hay = " ".join(node.strings("compatible") + [node.name]).lower()
    for cls, rx in CLASS_HINTS:
        if re.search(rx, hay):
            return cls
    return "other"


def build_spec(root: Node, src_id: str, compat_map: dict) -> dict:
    by_phandle = {n.u32("phandle"): n for n in root.walk() if "phandle" in n.props}
    for n in root.walk():
        if "linux,phandle" in n.props:
            by_phandle.setdefault(n.u32("linux,phandle"), n)
    src = lambda n: [f"dts:{src_id}:{n.path}"]

    spec = {"soc": {"compatible": root.strings("compatible"), "model": (root.strings("model") or [""])[0],
                    "src": [f"dts:{src_id}:/"]},
            "cpus": [], "interrupt_controller": {}, "memory": [], "peripherals": [],
            "chosen": {}, "unparsed": []}

    cpus = next((c for c in root.children if c.name == "cpus"), None)
    if cpus:
        groups: dict[str, dict] = {}
        for c in cpus.walk():
            if c.strings("device_type") == ["cpu"] or c.name.startswith("cpu@"):
                t = (c.strings("compatible") or ["unknown"])[0].split(",")[-1]
                g = groups.setdefault(t, {"type": t, "count": 0, "mpidr": [], "enable_method": None, "src": src(c)})
                g["count"] += 1
                g["mpidr"].append(hex(join(c.cells("reg"))))
                g["enable_method"] = (c.strings("enable-method") or [None])[0]
        spec["cpus"] = list(groups.values())
    psci = next((n for n in root.walk() if any("psci" in s for s in n.strings("compatible"))), None)
    if psci:
        spec["psci"] = {"compatible": psci.strings("compatible"), "method": (psci.strings("method") or [None])[0],
                        "src": src(psci)}

    for n in root.walk():
        if n.parent is None or n.path.startswith(("/cpus", "/__")) or n.name in ("chosen", "aliases"):
            continue
        status = (n.strings("status") or ["okay"])[0]
        compat = n.strings("compatible")
        if n.strings("device_type") == ["memory"] or n.name.startswith("memory@"):
            for a, s in regs(n):
                spec["memory"].append({"name": n.name, "kind": "ram", "base": a, "size": s, "src": src(n)})
            continue
        if n.parent.name == "reserved-memory":
            for a, s in regs(n):
                spec["memory"].append({"name": n.name, "kind": "reserved", "base": a, "size": s,
                                       "no_map": "no-map" in n.props, "src": src(n)})
            continue
        if "interrupt-controller" in n.props and any("gic" in s for s in compat) and not spec["interrupt_controller"]:
            r = regs(n)
            v3 = any("v3" in s for s in compat)
            spec["interrupt_controller"] = {
                "type": "gicv3" if v3 else ("gic400" if any("400" in s for s in compat) else "gicv2"),
                "compatible": compat,
                "regions": [{"base": a, "size": s} for a, s in r],
                **({"dist_base": r[0][0], "redist_base": r[1][0]} if v3 and len(r) >= 2 else {}),
                **({"dist_base": r[0][0], "cpu_if_base": r[1][0]} if not v3 and len(r) >= 2 else {}),
                "src": src(n),
            }
            continue
        r = regs(n)
        if not r:
            if compat and ("reg" in n.props):
                spec["unparsed"].append({"node": n.path, "why": "reg not CPU-addressable (no ranges)", "compatible": compat})
            continue
        if not compat:
            continue
        match = next((compat_map[c] | {"compatible": c} for c in compat if c in compat_map), None)
        p = {
            "name": n.name.split("@")[0] + "@" + hex(r[0][0])[2:],
            "class": guess_class(n),
            "compatible": compat,
            "base": r[0][0],
            "size": r[0][1],
            "status": status,
        }
        if len(r) > 1:
            p["extra_regions"] = [{"base": a, "size": s, "name": nm} for (a, s), nm in
                                  zip(r[1:], (n.strings("reg-names") or [""] * len(r))[1:] + [""] * len(r))]
        irqs = decode_irqs(n, by_phandle)
        if irqs:
            p["irqs"] = irqs
        clks = decode_clocks(n, by_phandle)
        if clks:
            p["clocks"] = clks
        for k in ("reg-shift", "reg-io-width", "clock-frequency", "bus-width", "#clock-cells", "#reset-cells"):
            if k in n.props:
                p.setdefault("dt_props", {})[k] = n.u32(k)
        if match:
            p["strategy"] = "reuse"
            p["qemu_model"] = match["qom_type"]
            p["reuse_fit"] = match.get("fit")
            if match.get("notes"):
                p["notes"] = match["notes"]
        p["src"] = src(n)
        spec["peripherals"].append(p)

    chosen = next((c for c in root.children if c.name == "chosen"), None)
    if chosen:
        spec["chosen"] = {k: (chosen.strings(k) or [None])[0] for k in ("stdout-path", "bootargs") if k in chosen.props}
    aliases = next((c for c in root.children if c.name == "aliases"), None)
    if aliases:
        spec["aliases"] = {k: v[0] for k, v in aliases.props.items() if v and isinstance(v[0], str)}

    spec["memory"].sort(key=lambda m: m["base"])
    spec["peripherals"].sort(key=lambda p: p["base"])
    return spec


class HexDumper(yaml.SafeDumper):
    pass


HexDumper.add_representer(int, lambda d, v: d.represent_scalar("tag:yaml.org,2002:int",
                                                               hex(v) if v > 0xffff else str(v)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dts", type=Path)
    ap.add_argument("-I", dest="includes", action="append", default=[])
    ap.add_argument("-o", dest="out", type=Path)
    ap.add_argument("--src-id")
    a = ap.parse_args()
    compat_map = yaml.safe_load((KIT / "reference" / "compat-map.yaml").read_text()) or {}
    root = parse(to_normalized_dts(a.dts, a.includes))
    spec = build_spec(root, a.src_id or a.dts.name, compat_map)
    text = yaml.dump(spec, Dumper=HexDumper, sort_keys=False, width=120)
    if a.out:
        a.out.write_text(text)
        n = spec["peripherals"]
        print(f"{a.out}: {len(spec['cpus'])} cpu groups, {len(spec['memory'])} memory, {len(n)} peripherals "
              f"({sum(1 for p in n if p.get('strategy') == 'reuse')} reuse candidates), "
              f"{len(spec['unparsed'])} unparsed")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
