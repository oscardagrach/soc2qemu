# QEMU SoC porting kit

Scaffolding for porting new Arm SoCs (Cortex-A and Cortex-M) to QEMU for full-system
emulation with minimal human input. Ports are local/downstream work on per-SoC branches and
are never submitted upstream (QEMU's `AGENTS.md` forbids agent-generated upstream
contributions; local experiments are permitted).

## Layout
- `config.env` - QEMU remote, base ref, targets, configure flags.
- `qemu-src/` - base upstream clone (never edit). `worktrees/<soc>/` - per-SoC worktree on
  branch `soc/<soc>`, build in `worktrees/<soc>/build/` (`build-kit/` on the libafl base, where
  libafl_qemu owns `build/`).
- `socs/<soc>/` - workspace: `inputs/` (untrusted user material), `intake/`, `spec/soc.yaml`
  (canonical SoC description with provenance), `qemu-base.env` (upstream or libafl base), `run.yaml` (boot config + milestones),
  `bringup/` (runs, LOG.md), `STATUS.md` (progress, decisions, questions for a human).
- `templates/soc-workspace/` - copied by `scripts/new-soc.sh`.
- `reference/qemu-conventions.md` - QEMU idioms and reference files (the tree wins on conflict).
- `reference/compat-map.yaml` - DT compatible -> existing QEMU model.
- `.claude/skills/` - `qemu-soc-port` (entry point), `qemu-soc-intake`, `qemu-soc-scaffold`,
  `qemu-device-model`, `qemu-bringup-debug`, `qemu-firmware-re`.
- `.claude/workflows/` - `soc-port` (end to end), `soc-intake`, `soc-devices`, `soc-bringup`.

## Scripts
| Script | Purpose |
|---|---|
| `scripts/setup-qemu.sh` | clone/fetch the base QEMU tree |
| `scripts/new-soc.sh <soc> [--base upstream\|libafl]` | workspace + worktree + configure; `libafl` bases the port on qemu-libafl-bridge for libafl_qemu fuzzing |
| `scripts/build.sh <soc> [--files a.c,b.c]` | serialized ninja build; `--files` reports only those files |
| `scripts/append-snippet.sh <file>` | locked, idempotent append to trace-events/meson.build/Kconfig |
| `scripts/dts2spec.py <dts/dtb> [-I dir]` | DTS -> partial spec (ranges, IRQs, clocks, reuse candidates) |
| `scripts/pdf2text.sh <soc> <pdf>` | TRM -> greppable text with page markers |
| `scripts/spec-check.py <soc>` | validate spec (overlaps, strategies, models exist in tree) |
| `scripts/fuzz-profile.py <soc>` | export `fuzz/profile.yaml` (address map, polled MMIO reads, snapshot candidates) for a fuzzing harness |
| `scripts/bringup-run.py <soc> [--label x] [-- qemu args]` | boot once; milestones, stall detection, PC hotspots via QMP, unimp/guest-error triage -> `bringup/runs/NNN/summary.{json,md}` |

Python scripts run through `uv` (inline dependencies); don't call them with bare `python3`.

## Rules
- Facts in `spec/soc.yaml` and model behaviour carry provenance (TRM page, BSP file:line,
  firmware address, or `guess:`).
- Parallel agents own disjoint files; shared build files only via `append-snippet.sh`, builds
  only via `build.sh`.
- Decide and record decisions in STATUS.md instead of asking; ask a human only after every
  source was tried, via STATUS.md "blocked on a human".
- Commit to `soc/<soc>` in the worktree at phase ends; never push.
- Self-test: `make -C tests/fixture-m3 && scripts/bringup-run.py selftest-m3` (uses system QEMU and `tests/fixture-m3`;
  expected outcome `stall` after milestone `stage1`, spinning on `i2s +0x4`).
  `selftest-m3-libafl` is the same check on the libafl base (needs `scripts/build.sh selftest-m3-libafl` first).
