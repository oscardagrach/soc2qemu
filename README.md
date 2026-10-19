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

## License

Dual-licensed under GPL-3.0-only or a commercial license; see [LICENSING.md](LICENSING.md).
Contributions require agreeing to the [CLA](CLA.md).
