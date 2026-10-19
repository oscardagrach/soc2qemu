/* SPDX-License-Identifier: GPL-3.0-only */
/* Self-test fixture for scripts/bringup-run.py: prints a banner on the mps2-an385 UART,
 * then spins polling a status bit in an unimplemented device - the classic bring-up hang. */
#include <stdint.h>
#define REG(a) (*(volatile uint32_t *)(a))
#define UART0 0x40004000u
#define I2S   0x40024000u   /* create_unimplemented_device("i2s") on mps2 */

static void puts_(const char *s) { while (*s) REG(UART0) = *s++; }

void reset(void)
{
    REG(UART0 + 8) = 1;                 /* CTRL.TXEN */
    puts_("fixture: stage1\n");
    REG(I2S) = 0x1234;                  /* "enable" */
    while (!(REG(I2S + 4) & 1)) { }     /* wait for READY that never comes */
    puts_("fixture: stage2\n");
    for (;;) { }
}

extern uint32_t _stack_top;
__attribute__((section(".vectors"))) const void *vectors[] = { &_stack_top, reset };
