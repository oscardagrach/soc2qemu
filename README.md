# QEMU SoC porting kit

Port a new Arm SoC to QEMU with Claude Code doing most of the work.

## Start a port
1. `scripts/new-soc.sh <soc>` (first run clones and configures QEMU).
2. Drop whatever you have into `socs/<soc>/inputs/`: DTS/DTB, TRM/datasheet PDFs, vendor
   BSP/SDK trees, firmware blobs or flash dumps, real-hardware boot logs, a NOTES.md.
3. In Claude Code from this directory: `/qemu-soc-port <soc>` - or ask it to run the
   `soc-port` workflow with `{soc: "<soc>"}`. Add `reviewSpec: true` to stop after intake
   so you can check `spec/soc.yaml` first.
4. Check `socs/<soc>/STATUS.md`. Anything that needs you is under "blocked on a human".

## Fuzzing

To fuzz a SoC with [libafl_qemu](https://github.com/AFLplusplus/LibAFL), start the port on
AFL++'s QEMU fork instead of upstream: `scripts/new-soc.sh <soc> --base libafl` (or
`base: "libafl"` in the `soc-port` workflow). The fork tracks an older QEMU (10.2.0 today), and
the base can't be switched after porting starts. After bring-up,
`scripts/fuzz-profile.py <soc>` writes `socs/<soc>/fuzz/profile.yaml`: the address map, MMIO
registers the firmware polls (input candidates), boot milestones and hot PCs (snapshot
candidates), and the QEMU tree to point `LIBAFL_QEMU_DIR` at. The harness itself lives outside
this kit.

## Resume or run one phase
- Workflows: `soc-intake`, `soc-devices`, `soc-bringup` (each takes `{soc}`), or `soc-port`
  with `from: "scaffold" | "devices" | "bringup"`.
- Manual loop: `scripts/build.sh <soc>` then `scripts/bringup-run.py <soc>` and read
  `socs/<soc>/bringup/runs/NNN/summary.md`.

## Pipeline
intake (spec with provenance) -> scaffold (SoC + board + reused models + unimp stubs) ->
devices (parallel new models, adversarial review, integration) -> bring-up loop (run,
PC-hotspot/unimp triage, parallel investigation, fixes, repeat until the last milestone in
`run.yaml` or no progress).

See `CLAUDE.md` for layout and rules.

## Example: finding a bring-up hang

Most bring-up stalls are firmware polling a status bit QEMU doesn't set. `bringup-run.py`
finds them without a debugger: it watches serial output for milestones, stops when output
goes quiet, samples every CPU's PC over QMP, disassembles the hot spot, and ranks the
unimplemented register accesses. Agents read the resulting `summary.json`; this is the
human-readable `summary.md` from the self-test (`tests/fixture-m3`, a Cortex-M3 program
that waits for a ready bit in an unmodeled device):

```
$ make -C tests/fixture-m3 && scripts/bringup-run.py selftest-m3
# selftest-m3 run 001
outcome: **stall** (exit 0, 8.58s), milestones 1/2 (last: stage1), serial 16 bytes

## serial (tail)
fixture: stage1

## pc hotspots
- cpu0: 0x30..0x30 100% of 30 samples (SPINNING); top [['0x30', 30]]
0x0000002c:  601a       str      r2, [r3]
0x0000002e:  461a       mov      r2, r3
0x00000030:  6853       ldr      r3, [r2, #4]
0x00000032:  f013 0f01  tst.w    r3, #1
0x00000036:  d0fb       beq      #0x30

## unimplemented devices (by access count)
- i2s: 5415306

## last distinct unimplemented accesses (most recent last)
- i2s write +0x0 size 4 x1 values 0x00001234
- i2s read +0x4 size 4 x5415305
```

The loop at `0x30` reads `i2s +0x4` and waits for bit 0. That register and bit are what the
model has to provide, which is the fix the bring-up loop's agents go looking for.

## License

Dual-licensed under GPL-3.0-only or a commercial license; see [LICENSING.md](LICENSING.md).
Contributions require agreeing to the [CLA](CLA.md).
