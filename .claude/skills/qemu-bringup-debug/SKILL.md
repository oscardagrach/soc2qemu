---
name: qemu-bringup-debug
description: Diagnose why a SoC's firmware/OS stops booting under QEMU and decide the smallest model change that gets it further - reads bringup-run.py summaries (milestones, PC hotspots, unimplemented accesses, guest errors) and traces each blocker to a register and a source. Use during the bring-up phase or whenever a run stalls, crashes, or loops.
---

# Bring-up diagnosis

One iteration = run, diagnose, fix, rerun. `scripts/bringup-run.py <soc> --label <why>`
writes `socs/<soc>/bringup/runs/NNN/summary.json` (+ `summary.md`, `serial*.log`,
`qemu.log`, `cmdline.txt`). Progress is `summary.progress` = [milestones reached, serial
bytes]; compare with the previous run. Log each iteration in `socs/<soc>/bringup/LOG.md`.

## Read the summary in this order
1. `outcome`: `success` (done), `stall` (serial went quiet), `timeout`, `qemu-exited`
   (look at `stdout_tail`: abort/assert = a model or machine bug, fix that first).
2. `pc_hotspots`: `spinning: true` with a small `hot_range` is a polling loop.
   `hot_disasm` shows the loop: find the load (`ldr`) whose address the loop compares -
   that MMIO register + bit is the blocker. If the address comes from a literal pool or a
   base register, compute it from the disassembly or use `qemu-firmware-re`.
   PC at a `wfi`/`wfe` = waiting for an interrupt (timer or device IRQ not wired/firing).
   PC in an exception vector or a `b .` = crashed: rerun with `log_flags: unimp,guest_errors,int`
   to get the exception class, faulting address (FAR) and PC.
3. `unimp_last`: the most recent distinct unimplemented accesses, usually the blocker
   when the PC is spinning nearby. `unimp_by_device`: what to model next by volume.
4. `log_messages`: guest errors (bad access sizes, writes to RO, unassigned accesses).
5. `serial_tail`: the last message often names the subsystem (DDR, clock, MMC, PMIC...).

## Common blockers and smallest fixes
| Symptom | Usual cause | Smallest fix |
|---|---|---|
| Spin reading a clock/PLL/sysctl register | lock / ready bit never sets | sysctl model: bit reads as 1 (source: TRM/BSP poll loop) |
| Spin in DDR init (SPL) | DDR PHY training status bits | DDR controller stub returning "done/pass" for polled bits; DRAM is plain RAM |
| Spin on UART status before printing | TX-ready / FIFO-not-full bit | reuse correct UART model or add the status bit (DW UART: USR @0x7c) |
| Silent: no serial at all | wrong reset vector/entry, console UART base/regshift, chardev not attached | check `cmdline.txt`, CPU rvbar/init-svtor, `serial_hd()` index |
| Stuck at `wfi` early | timer IRQ not wired, CNTFRQ 0, wrong PPI | wire generic timer PPIs, set `cntfrq` |
| Hang after "Starting kernel" | earlycon base wrong, DTB mismatch, PSCI conduit, secondary CPUs | check DTB passed, PSCI mode vs firmware, `start-powered-off` |
| Linux probe errors/timeouts | device modeled partially | model the next registers that driver touches (`unimp_accesses` for that device) |
| Reset loop | watchdog firing or SoC reset register | model watchdog as disabled or implement reset correctly |
| Boot ROM gives up on media | boot-media controller or image header/signature | prefer entry_mode `firmware` (skip the ROM) unless the ROM is the target |
| Crypto/signature check fails | secure boot in ROM/SPL | model the fuse/OTP as "secure boot disabled", or skip the stage |

## Rules
- Every fix cites its source: TRM page, BSP file:line, or firmware address showing the
  poll. A guess is allowed only when nothing else exists; mark it `guess:` in a code
  comment and in LOG.md so it can be revisited.
- Prefer the smallest model change that matches real hardware semantics. Don't hack the
  firmware image, don't skip instructions with gdb, don't special-case PCs in QEMU.
- One blocker per fix batch when blockers are related; independent devices can be fixed
  in parallel (different files).
- If two consecutive iterations make no progress on the same blocker, change approach:
  go to `qemu-firmware-re` for the exact expected value, read the BSP driver for that
  register, or switch `boot.entry_mode` to bypass the stage. After three, record it in
  STATUS.md "blocked on a human" with everything tried.

## Deeper tools
- `-d unimp,guest_errors,int,cpu_reset` via `log_flags` in run.yaml.
- Trace a model: `-trace '<soc>_*'` in run.yaml args (needs trace events in the model).
- GDB: add `-s -S` to args, run QEMU yourself in tmux, `gdb -ex 'target remote :1234'`
  (check `gdb --configuration` lists arm/aarch64 support), break at the hot PC, inspect registers.
- `info mtree -f` through QMP `human-monitor-command` to confirm the memory map.
