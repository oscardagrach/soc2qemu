---
name: qemu-device-model
description: Write or extend one QEMU peripheral model for a SoC port (register file, behaviour firmware depends on, IRQs, clocks, reset, vmstate, trace events, qtest), safely in parallel with other agents. Use when a peripheral has strategy new, or a bring-up diagnosis says a model needs a register or behaviour.
---

# Model one peripheral

Inputs: SoC name, peripheral name (an entry in `spec/soc.yaml`), and optionally a
diagnosis describing exactly what the firmware needs. Work in `worktrees/<soc>`.
Read `reference/qemu-conventions.md` and open `hw/misc/max78000_gcr.c` (or a closer
existing model of the same class) to copy current idioms.

## Scope: model what the firmware observes, nothing more
1. Collect the register list: TRM register tables, BSP driver register defines/structs,
   and what the firmware actually touches (`qemu-firmware-re`, or the `unimp_accesses` of
   the latest `bringup/runs/*/summary.json` for this device's name).
2. Classify each register: plain storage (read back what was written), read-only status
   (reset value; which bits firmware polls and what makes them set), write-1-to-clear,
   self-clearing command bits, FIFOs/data ports, interrupt status/enable/mask.
3. Implement behaviour only where the firmware depends on it. Status bits firmware waits
   on should become true immediately (or after a short `QEMU_CLOCK_VIRTUAL` timer if
   ordering matters). Unknown registers: storage + `LOG_UNIMP` on first access.
4. If a well-known IP block is inside (DesignWare, Cadence, Arm PrimeCell, SDHCI core),
   reuse the existing model and wrap it, instead of rewriting it.

## Files (yours alone - never edit another agent's device files)
- `hw/<class-dir>/<soc>_<dev>.c`, `include/hw/<class-dir>/<soc>_<dev>.h`
  (class dirs: char, timer, misc, gpio, i2c, ssi, sd, net, dma, intc, watchdog, rtc...).
- Shared files only through the lock helper, never with Edit:
  `scripts/append-snippet.sh hw/<dir>/trace-events <<'X' ... X`, same for that dir's
  `meson.build` and `Kconfig`. Wiring into `hw/arm/<soc>.c` is done by the integration
  step unless your task says you own it.
- Mandatory: `REG32`/`FIELD` defines, reset hold phase restoring reset values, VMState,
  trace events for reads/writes, `LOG_GUEST_ERROR` for bad offsets/sizes, MMIO ops
  `.valid/.impl` sizes matching the hardware.

## Verify
1. `scripts/build.sh <soc> --files <your .c files>` - the result covers only your files, so
   failures in other agents' devices don't block you. Repeat until clean.
2. Add a qtest when the device has testable behaviour (`tests/qtest/<soc>_<dev>-test.c`,
   registered in `tests/qtest/meson.build` like the stm32l4x5 tests) once the device is
   wired into the machine. Run it with
   `cd worktrees/<soc>/build && QTEST_QEMU_BINARY=./qemu-system-aarch64 ./tests/qtest/<soc>_<dev>-test`.
3. Return: files written, registers modeled, behaviours assumed (each with its source),
   anything left as LOG_UNIMP.
