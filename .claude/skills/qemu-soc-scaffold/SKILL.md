---
name: qemu-soc-scaffold
description: Generate the QEMU SoC container, board/machine, Kconfig/meson wiring, reused devices and unimplemented-device stubs from socs/<soc>/spec/soc.yaml, build it, and do the first boot run. Use after intake passes spec-check.
---

# Scaffold a SoC in QEMU

Input: `socs/<soc>/spec/soc.yaml` (passes `scripts/spec-check.py`). Work in
`worktrees/<soc>`. Follow `reference/qemu-conventions.md`, and copy idioms from the
reference file it names for this profile and base (`socs/<soc>/qemu-base.env`; Cortex-A:
`hw/arm/ax3000-soc.c` upstream, `hw/arm/fsl-imx8mp.c` on the libafl base; Cortex-M:
`hw/arm/max78000_soc.c`) - open it before writing anything.

## Files (names use `<soc>` with `-` -> `_` for C identifiers)
- `include/hw/arm/<soc>.h` - SoC state struct (`<Soc>State`), memmap enum + table,
  IRQ numbers, child device members.
- `hw/arm/<soc>.c` - SoC container (`TYPE_SYS_BUS_DEVICE` parent, like both references): instance_init
  creates children, realize sets props/maps/wires them.
- `hw/arm/<soc>-boards.c` (or `<board>.c`) - `MachineClass` for `soc.board_name`: RAM,
  boot image loading per `boot.entry_mode`, `default_cpus`, `min/max_cpus`.
- `hw/arm/Kconfig`, `hw/arm/meson.build` - entries mirroring neighbours.

## What realize must contain
1. Memory: ROM/SRAM/DRAM regions from `memory` (`memory_region_init_rom` /
   `memory_region_init_ram`; DRAM from `machine->ram`).
2. CPUs exactly as the spec says (types, count, MPIDR affinity, has_el3/el2, rvbar /
   init-svtor = `boot.reset_vector`, secondaries `start-powered-off` if the ROM releases them).
3. Interrupt controller and CPU wiring (timer PPIs, maintenance IRQ, IRQ/FIQ/VIRQ/VFIQ).
4. Every `strategy: reuse` peripheral: instantiate its `qemu_model`, set props
   (e.g. `serial-mm` `regshift`), map MMIO, connect IRQs. Console UART gets `serial_hd(0)`
   (`qdev_prop_set_chr(dev, "chardev", serial_hd(i))`) in the order of `run.yaml` serials.
5. Every `strategy: new` peripheral: create a `create_unimplemented_device` placeholder now,
   tagged with a `/* TODO(soc-devices): <name> */` comment. The devices phase swaps it.
6. Every `strategy: stub` peripheral: `create_unimplemented_device("<soc>.<name>", base, size)`.
7. Catch-all: one unimplemented device over each whole peripheral window so unlisted
   registers log instead of faulting. `create_unimplemented_device` maps at priority -1000,
   the same as the per-peripheral stubs, and overlapping regions at equal priority have no
   defined winner. So build the catch-all by hand at a lower priority: create a
   `TYPE_UNIMPLEMENTED_DEVICE`, set `name` and `size`, realize it, then
   `sysbus_mmio_map_overlap(sbd, 0, base, -2000)` (see `include/hw/misc/unimp.h`).

## Build and first run
1. `scripts/build.sh <soc>` until `BUILD OK`. Fix only your files.
2. Fill `socs/<soc>/run.yaml`: `machine`, `qemu_binary`, `args` for the boot images in
   `inputs/`, `serial_count`, milestones (from intake).
3. `scripts/bringup-run.py <soc> --label scaffold`. Any outcome is fine as long as QEMU
   starts and the summary is produced; a "qemu-exited" with an error in stdout means fix
   the machine first.
4. Commit in the worktree (`<soc>: scaffold SoC and board`), update STATUS.md
   (scaffold: done, first run number and outcome).
