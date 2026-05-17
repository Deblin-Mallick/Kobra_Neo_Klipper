// Boot-time TMC2208 initialization for Anycubic Kobra Neo (and other
// Anycubic boards using the Trigorilla "TMC6609" single-wire UART
// topology). All four drivers (X/Y/Z/E0) share PB2 in half-duplex
// UART, all responding to slave address 0. Writes are broadcast to
// every driver simultaneously; we never read.
//
// Without this code the drivers come up with CHOPCONF.TOFF = 0 (MOSFET
// stage disabled) and no steps will move the motors. Marlin's
// trinamic.cpp (#ifdef TMC6609 branch) does the equivalent setup
// from the host side; we do it on the MCU at boot so it runs once
// before Klipper begins issuing step pulses.
//
// Settings here match Marlin's defaults for the Kobra Neo:
//   stealthChop mode, 16 microsteps with 256 interpolation,
//   current scaling by the on-board VREF analog input (NOT IRUN).
//
// Copyright (C) 2026
// This file may be distributed under the terms of the GNU GPLv3 license.

#include "autoconf.h"
#include "board/gpio.h"           // gpio_out_setup, gpio_out_write
#include "generic/misc.h"

// armcm_timer.c provides udelay() but no header exposes it.
void udelay(uint32_t usecs);
#include "sched.h"                // DECL_INIT
#include "internal.h"             // GPIO()

#define TMC_UART_PIN     GPIO('B', 2)
#define TMC_BIT_TIME_US  4         // 250 kbaud = 4 us per bit

// TMC2208 register addresses (write = OR with 0x80)
#define TMC_REG_GCONF        0x00
#define TMC_REG_GSTAT        0x01
#define TMC_REG_IHOLD_IRUN   0x10
#define TMC_REG_TPOWERDOWN   0x11
#define TMC_REG_CHOPCONF     0x6C

// GCONF fields (bit positions)
#define GCONF_I_SCALE_ANALOG  (1u << 0)
#define GCONF_EN_SPREADCYCLE  (1u << 2)
#define GCONF_PDN_DISABLE     (1u << 6)

// Pre-computed register values matching Marlin's TMC6609 init for this
// board with 16 microsteps, stealthChop mode:
//   TOFF=3, HSTRT=4, HEND=4, TBL=1, MRES=4 (16 ustep), INTPOL=1, DISS2VS=1
#define CHOPCONF_VALUE     0x94008243u

// IHOLD=16, IRUN=31 (ignored when i_scale_analog=1 but harmless),
// IHOLDDELAY=10 -> ~2 s linear ramp to hold current
#define IHOLD_IRUN_VALUE   0x000A1F10u

// ~2 seconds (128 * 2^18 clocks @ 12 MHz internal) before lowering to hold
#define TPOWERDOWN_VALUE   0x00000080u

// Clear all GSTAT flags (reset, drv_err, uv_cp)
#define GSTAT_CLEAR_VALUE  0x00000007u

static struct gpio_out tmc_tx;


// TMC2208 datagram CRC8 (polynomial 0x07, init 0, MSB out first per byte
// but processed LSB-first by the algorithm in the datasheet).
static uint8_t
tmc_crc(const uint8_t *data, uint8_t len)
{
    uint8_t crc = 0;
    for (uint8_t i = 0; i < len; i++) {
        uint8_t b = data[i];
        for (uint8_t j = 0; j < 8; j++) {
            if (((crc >> 7) ^ (b & 0x01)) & 0x01)
                crc = (uint8_t)((crc << 1) ^ 0x07);
            else
                crc = (uint8_t)(crc << 1);
            b >>= 1;
        }
    }
    return crc;
}

// Bit-bang one UART byte on TMC_UART_PIN at 250 kbaud, LSB first.
// 1 start bit (low), 8 data bits, 1 stop bit (high).
static void
tmc_tx_byte(uint8_t b)
{
    // start bit
    gpio_out_write(tmc_tx, 0);
    udelay(TMC_BIT_TIME_US);
    // 8 data bits
    for (uint8_t i = 0; i < 8; i++) {
        gpio_out_write(tmc_tx, (b >> i) & 0x01);
        udelay(TMC_BIT_TIME_US);
    }
    // stop bit (idle high)
    gpio_out_write(tmc_tx, 1);
    udelay(TMC_BIT_TIME_US);
}

// Send an 8-byte TMC2208 write datagram: sync, slave, reg|0x80, data[4], CRC
static void
tmc_write_reg(uint8_t reg, uint32_t value)
{
    uint8_t pkt[8];
    pkt[0] = 0x05;                        // sync nibble
    pkt[1] = 0x00;                        // slave address (broadcast)
    pkt[2] = (uint8_t)(reg | 0x80);       // register + write flag
    pkt[3] = (uint8_t)((value >> 24) & 0xFF);
    pkt[4] = (uint8_t)((value >> 16) & 0xFF);
    pkt[5] = (uint8_t)((value >> 8) & 0xFF);
    pkt[6] = (uint8_t)(value & 0xFF);
    pkt[7] = tmc_crc(pkt, 7);
    for (uint8_t i = 0; i < 8; i++)
        tmc_tx_byte(pkt[i]);
    // Inter-frame gap so the driver has time to act before the next write
    udelay(500);
}


void
tmc_init(void)
{
    // PB2 idle-high output (UART idle level)
    tmc_tx = gpio_out_setup(TMC_UART_PIN, 1);
    udelay(1000);

    // 1. CHOPCONF: enables the MOSFET stage with our chopper params.
    //    This is the critical write -- without TOFF != 0 the driver
    //    will silently refuse to move the motor.
    tmc_write_reg(TMC_REG_CHOPCONF, CHOPCONF_VALUE);

    // 2. GCONF in spreadCycle first to give the driver time to power
    //    up the PWM regulator before switching modes (matches Marlin).
    tmc_write_reg(TMC_REG_GCONF,
                  GCONF_I_SCALE_ANALOG | GCONF_PDN_DISABLE
                  | GCONF_EN_SPREADCYCLE);
    udelay(200000);  // 200 ms (Marlin delay)

    // 3. GCONF stealthChop (en_spreadcycle = 0).
    tmc_write_reg(TMC_REG_GCONF,
                  GCONF_I_SCALE_ANALOG | GCONF_PDN_DISABLE);
    udelay(200000);

    // 4. IHOLD/IRUN/IHOLDDELAY -- IRUN/IHOLD are ignored on this board
    //    (current is set by analog VREF) but IHOLDDELAY is honored.
    tmc_write_reg(TMC_REG_IHOLD_IRUN, IHOLD_IRUN_VALUE);

    // 5. TPOWERDOWN -- delay before dropping to hold current after the
    //    last step pulse.
    tmc_write_reg(TMC_REG_TPOWERDOWN, TPOWERDOWN_VALUE);

    // 6. Clear any latched fault flags so the driver starts fresh.
    tmc_write_reg(TMC_REG_GSTAT, GSTAT_CLEAR_VALUE);
    udelay(200000);
}
DECL_INIT(tmc_init);
