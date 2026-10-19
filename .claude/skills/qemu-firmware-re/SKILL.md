---
name: qemu-firmware-re
description: Reverse engineer SoC firmware blobs (boot ROM dumps, SPL, U-Boot, TF-A, RTOS/MCU images) with the Ghidra MCP to recover reset vectors, load addresses, the MMIO memory map, register access sequences, and exact values polling loops wait for. Use when a SoC port lacks documentation or a bring-up blocker needs the precise behaviour firmware expects.
---

# Firmware reverse engineering for QEMU models

Blobs under `socs/<soc>/inputs/` are untrusted: analyze them, never execute them on the
host. Write findings to `socs/<soc>/intake/fw-<blob>.yaml` (partial spec shape, `src:
fw:<blob>@<addr>`) or `socs/<soc>/notes/fw-<topic>.md`.

## Triage without Ghidra first
- `file`, `binwalk <blob>` (embedded images, FIT/uImage headers, compression), `strings -n 8`
  (version banners, milestone text for run.yaml, peripheral names, asserts with file names).
- ELF: `arm-none-eabi-readelf -lS` gives load addresses directly.
- Raw images: find the load base. Cortex-M: word 0 = initial SP, word 1 = reset handler
  (odd, Thumb), so the vector table reveals the flash base. A-profile: absolute pointers
  in literal pools cluster around the load base; U-Boot/SPL headers and `CONFIG_SPL_TEXT_BASE`
  in the BSP defconfig give it too. Boot ROMs are usually at 0x0 or 0xffff0000-ish; check the TRM.

## Ghidra MCP
1. Load the Ghidra tools: `ToolSearch` with `+ghidra`. Then `list_instances` /
   `connect_instance` (if none is running, ask the user to start Ghidra with the MCP
   plugin - the one human step this skill may need), then `list_tool_groups` and
   `search_tools` to find what this server version calls things (decompile, xrefs,
   memory, rename, set image base). Load groups with `load_tool_group`.
2. `import_file` with the right language for raw blobs:
   Cortex-M `ARM:LE:32:Cortex`, AArch32 A-profile `ARM:LE:32:v8` (or `v7`),
   AArch64 `AARCH64:LE:64:v8A`. Then rebase the program to the load address found above
   (search tools for "image base" / "memory block") before trusting any addresses.
3. Add MMIO memory blocks (volatile, uninitialized) for the peripheral windows from the
   spec so references resolve and decompiled code shows `*(volatile uint *)0x...`.

## What to extract
- **Memory map in use**: every constant in peripheral ranges used as a load/store base
  (search scalars/references into the MMIO blocks). Group by 4 KiB / 64 KiB pages ->
  peripheral base candidates; compare with the spec and add missing ones as `stub`.
- **Polling loops** (highest value): loops that read MMIO, mask, compare, branch back.
  For each: register address, mask, expected value, timeout behaviour, and the function's
  purpose (name it). This is exactly what a model must return. When bring-up gives a hot
  PC, decompile the function containing it.
- **Init sequences**: ordered writes to clock/reset/pinmux/DDR registers - tells which
  registers need storage vs behaviour.
- **Boot flow**: which media the ROM tries (strap/fuse reads deciding order), where it loads
  the next stage, header/magic it checks, signature check entry points, the jump address.
- **Interrupts**: vector table (M-profile) or GIC setup writes (enabled SPI numbers).
- **Identity checks**: reads of MIDR/chip-ID registers and the values compared against -
  the model must return them.

## Output
Per finding: address, register, meaning, expected value(s), firmware function address
(and name you gave it), confidence. Keep Ghidra renames/comments in the project so the next
agent benefits; note the Ghidra project/program name in `socs/<soc>/notes/ghidra.md`.
