# QEMU conventions for SoC ports

Checked against upstream QEMU 11.1.50 (master, 2026-10-16). QEMU's internal APIs churn every
release. **The tree in `worktrees/<soc>` is the authority, not this file.** Before writing a
file, open the reference file listed below and copy its current idioms (includes, class_init
signature, property arrays, reset). If this document disagrees with the tree, follow the tree
and fix this document.

## QEMU base

Each SoC records its base in `socs/<soc>/qemu-base.env`:

- `QEMU_BASE=upstream` (default): upstream master. Everything below applies as written.
- `QEMU_BASE=libafl`: AFL++'s `qemu-libafl-bridge` at the commit libafl_qemu pins (QEMU 10.2.0
  + LibAFL patches, checked 2026-10-18), so the port can be fuzzed with libafl_qemu. Differences
  from upstream that matter for new code:
  - headers are at the old paths: `hw/sysbus.h`, `hw/qdev-core.h`, `hw/qdev-properties.h`,
    `hw/irq.h`, `hw/registerfields.h` (not `hw/core/...`);
  - `hw/arm/ax3000-*` does not exist; use `fsl-imx8mp.c` / `imx8mp-evk.c` as the Cortex-A reference;
  - `class_init(ObjectClass *, const void *)`, `const Property[]` without END_OF_LIST, and
    `arm_common_ss` are the same as upstream.
  Never edit LibAFL's own patches in the tree; add SoC code next to them.
  The kit builds in `build-kit/` on this base, because libafl_qemu configures and builds its
  own copy of QEMU in `build/` when it is pointed at the tree with `LIBAFL_QEMU_DIR`.

## Reference files to copy from

| Need | Copy from | Why |
|---|---|---|
| Small modern Cortex-A SoC (A53 + GICv3, timers wired to PPIs) | `hw/arm/ax3000-soc.c`, `ax3000-evk.c`, `ax3000-boards.c` | added 2026-08, ~300 lines, current idioms |
| Larger Cortex-A SoC with memmap table, many devices, unimp fill | `hw/arm/fsl-imx8mp.c`, `hw/arm/imx8mp-evk.c` | `fsl_imx8mp_memmap[]` + loop creating unimp for unmodeled entries |
| Cortex-M SoC | `hw/arm/max78000_soc.c` (2025), `hw/arm/stm32l4x5_soc.c` | `TYPE_ARMV7M`, clocks, NVIC wiring |
| Board loading a vendor boot ROM (`-bios`) into a ROM region | `hw/arm/npcm7xx_boards.c` (`load_image_mr` into `soc->irom`) | firmware-chain boots |
| Boot ROM + flash-based boot chain | `hw/arm/aspeed.c` (`aspeed_load_vbootrom`) | |
| Simple peripheral with register file, reset, vmstate, trace | `hw/misc/max78000_gcr.c` | 2025, current idioms |
| qtest for a peripheral | `tests/qtest/stm32l4x5_*-test.c`, `tests/qtest/npcm7xx_*-test.c` | |
| Functional (boot) test | `tests/functional/arm/test_*.py`, `tests/functional/aarch64/` | |

Find more recent examples: `git log --diff-filter=A --format='%cs %h %s' -- 'hw/arm/*.c' | head`.

## Current idioms (verify in the reference file)

- Headers moved to `include/hw/core/` (e.g. `hw/core/sysbus.h`, `hw/core/qdev.h`,
  `hw/core/qdev-properties.h`, `hw/core/irq.h`, `hw/core/registerfields.h`, `hw/core/boards.h`).
  Copy the include list from a recently added file.
- `class_init` signature is `static void foo_class_init(ObjectClass *oc, const void *data)`.
- Property arrays are `static const Property foo_props[] = { ... };` with **no**
  `DEFINE_PROP_END_OF_LIST` (removed); install with `device_class_set_props(dc, foo_props)`.
- Type declaration: `OBJECT_DECLARE_SIMPLE_TYPE(FooState, FOO)` in the header, `TypeInfo` +
  `type_init()` or `DEFINE_TYPES()` in the .c file.
- Reset: prefer three-phase reset, `ResettableClass *rc = RESETTABLE_CLASS(oc);
  rc->phases.hold = foo_reset_hold;` with `static void foo_reset_hold(Object *obj, ResetType type)`.
  (`device_class_set_legacy_reset` still exists in older files; don't use it in new code.)
- Children: `object_initialize_child(obj, "name", &s->child, TYPE_X)` in `instance_init`, then
  `sysbus_realize(SYS_BUS_DEVICE(&s->child), errp)` / `qdev_realize` in the parent's realize.
- Errors in realize: `Error **errp`, return early on failure; `&error_fatal` only in board code.
- Registers: `hw/core/registerfields.h` macros `REG32(NAME, off)`, `FIELD(NAME, FIELD, shift, len)`,
  `FIELD_EX32/FIELD_DP32`, `ARRAY_FIELD_EX32/ARRAY_FIELD_DP32` over `uint32_t regs[N]`.
- Unmodeled register access: `qemu_log_mask(LOG_UNIMP, ...)`; guest misuse: `LOG_GUEST_ERROR`.
  Name the device and offset in the message.
- Tracing: add events to the directory's `trace-events` (`foo_read(uint64_t off, uint64_t val) "off 0x%" PRIx64 " val 0x%" PRIx64`),
  include `"trace.h"`, call `trace_foo_read(...)`.
- Migration: every device with state gets a `VMStateDescription` (`dc->vmsd`). Bump
  `version_id` only once upstreamed; during bring-up keep version 1.
- Clocks: `qdev_init_clock_in(DEVICE(obj), "clk", cb, s, ClockUpdate)` in the device;
  board/SoC creates `clock_new()` + `clock_set_hz()` and connects with `qdev_connect_clock_in`.
- Timers: `ptimer` (`hw/core/ptimer.h`) for down-counters; `QEMUTimer` with
  `QEMU_CLOCK_VIRTUAL` for everything else. Never use host time.

## Build integration

- `hw/arm/meson.build` uses `arm_common_ss.add(when: 'CONFIG_FOO_SOC', if_true: files(...))`
  for boards; device directories use their own source sets (`system_ss.add(...)` in
  `hw/misc/meson.build` etc.). Mirror the neighbouring lines exactly.
- Kconfig: board entry in `hw/arm/Kconfig` (`config FOO_EVB` with `default y`,
  `depends on TCG && AARCH64` or `ARM` as neighbours do, `select FOO_SOC`); the SoC entry
  `select`s every device config it instantiates (`ARM_GIC`, `PL011`, `UNIMP`, `SERIAL_MM`, ...).
  Device configs live in the device directory's Kconfig.
- Shared files (`trace-events`, `meson.build`, `Kconfig`) are appended through
  `scripts/append-snippet.sh` when several agents work in parallel; builds go through
  `scripts/build.sh` (serialized, `ninja -k 0`).

## Arm specifics

### Cortex-A
- CPU: `object_initialize_child(obj, "cpu[*]", &s->cpu[i], ARM_CPU_TYPE_NAME("cortex-a53"))`
  (type string becomes `"cortex-a53-arm-cpu"`). Available models: `git grep '"cortex-' target/arm`.
  Set `has_el3`/`has_el2` (only if `object_property_find` says the property exists),
  `mp-affinity` per CPU to match the real MPIDR (the DTS `reg` of each cpu node), `rvbar`
  when the boot ROM is not at 0, `start-powered-off` for secondaries the boot ROM releases.
- No exact CPU model (e.g. cortex-a520)? Use the closest existing model with the same
  architecture features the firmware checks (often reads MIDR); record the substitution in
  STATUS.md. Adding a new CPU model is a separate, upstreamable change in `target/arm/`.
- GIC: `gicv3_class_name()` / `gic_class_name()`; `num-irq` = SPIs + 32 (`GIC_INTERNAL`);
  `revision`, `num-cpu`, `redist-region-count`, `has-security-extensions` (true if EL3).
  Device IRQ wiring uses the DTS SPI number directly: `qdev_get_gpio_in(gic, spi)`.
- Generic timer outputs and GIC maintenance IRQ go to per-CPU PPIs: copy the loop in
  `ax3000-soc.c` / `fsl-imx8mp.c` verbatim and adjust the PPI numbers from the DTS
  `arm,armv8-timer` node.
- Generic timer frequency: CPU property `cntfrq` (match `CNTFRQ_EL0` the firmware expects;
  many boot ROMs program it but some rely on reset value).
- PSCI: with `-kernel`, QEMU emulates PSCI itself (`psci_conduit = QEMU_PSCI_CONDUIT_SMC`).
  With a real firmware chain (TF-A/BL31 in the images), leave PSCI to the firmware:
  `psci_conduit = QEMU_PSCI_CONDUIT_DISABLED` and `has_el3 = true`.

### Cortex-M
- `TYPE_ARMV7M` container, props `cpu-type` (`ARM_CPU_TYPE_NAME("cortex-m4")`), `num-irq`,
  `init-svtor` (vector table base at reset), `enable-bitband`; link `memory` to the SoC's
  system memory region; connect clocks `cpuclk` and `refclk` (SysTick).
- Peripheral IRQs: `qdev_get_gpio_in(DEVICE(&s->armv7m), n)` with the NVIC external IRQ number.
- Board loads the image with `armv7m_load_kernel(cpu, machine->kernel_filename, base, size)`.

## Boot modes (pick per spec `boot.entry_mode`)

- **bootrom-image**: model the boot ROM as a ROM region; `-bios` loads the dumped ROM there
  (`load_image_mr`); CPUs reset to the ROM address (`rvbar` / `init-svtor`). Boot media
  (SPI-NOR, eMMC, SD) must then be modeled well enough for the ROM to load the next stage.
- **firmware**: skip the ROM; load the first mutable stage (SPL/TF-A BL2) directly into SRAM
  with `-device loader,file=...,addr=...` or a board `-bios` hook, and set the CPU entry.
  Often the fastest path when the ROM is unavailable or does crypto.
- **kernel**: `arm_load_kernel()` with `struct arm_boot_info` (`loader_start` = DRAM base,
  `ram_size`, `psci_conduit`, `modify_dtb` to patch the DTB). Fastest path to a Linux shell.
- **elf**: bare-metal / RTOS ELF via `-kernel` (Cortex-M: `armv7m_load_kernel`).

## Stub strategy

- Every address range the firmware may touch and we don't model gets
  `create_unimplemented_device("soc.name", base, size)` so accesses log under `-d unimp`
  instead of faulting. Put it in place first, then replace stubs with models one by one.
- Many boot blockers are a single status bit (PLL lock, DDR init done, reset deassert ack,
  FIFO not full). A small "sysctl" model with a register file and a table of
  read-as-ones / self-clearing bits beats modeling the whole block. Keep such tables in one
  place per SoC and document each bit's source (TRM page, firmware address).

## Upstream policy

QEMU's `AGENTS.md` / `docs/devel/code-provenance.rst` forbid agent-generated code in upstream
contributions. Ports made with this kit are local/downstream emulation work on `soc/<soc>`
branches and are never submitted upstream. Still keep the code clean (SPDX header
`/* SPDX-License-Identifier: GPL-2.0-or-later */`, `scripts/checkpatch.pl` mostly clean, one
device per commit) so it stays maintainable when rebasing onto newer QEMU.
