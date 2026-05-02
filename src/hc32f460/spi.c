// SPI functions on HC32F460
//
// Copyright (C) 2024
// This file may be distributed under the terms of the GNU GPLv3 license.

#include "board/io.h"
#include "command.h"
#include "gpio.h"
#include "internal.h"
#include "sched.h"
#include <hc32f460_spi.h>
#include <hc32f460_gpio.h>
#include <hc32f460_pwc.h>

// SPI2 on the Kobra Neo: SCK=PC5, MOSI=PC4, MISO=unused
// Klipper bus index 0 = SPI2 (the only SPI used on this board)
DECL_ENUMERATION("spi_bus", "spi2", 0);
DECL_CONSTANT_STR("BUS_PINS_spi2", "PC4,PC5");

static uint8_t spi_initialized = 0;

static void
spi_hw_init(void)
{
    if (spi_initialized)
        return;

    stc_spi_init_t cfg;
    memset(&cfg, 0, sizeof(cfg));

    // Enable SPI2 peripheral clock
    PWC_Fcg1PeriphClockCmd(PWC_FCG1_PERIPH_SPI2, Enable);

    // Configure SPI2 pins
    PORT_SetFunc(PortC, Pin05, Func_Spi2_Sck, Disable);
    PORT_SetFunc(PortC, Pin04, Func_Spi2_Mosi, Disable);

    // SPI2 master config
    cfg.enClkDiv = SpiClkDiv2;
    cfg.enFrameNumber = SpiFrameNumber1;
    cfg.enDataLength = SpiDataLengthBit8;
    cfg.enFirstBitPosition = SpiFirstBitPositionMSB;
    cfg.enSckPolarity = SpiSckIdleLevelLow;
    cfg.enSckPhase = SpiSckOddSampleEvenChange;
    cfg.enReadBufferObject = SpiReadReceiverBuffer;
    cfg.enWorkMode = SpiWorkMode4Line;
    cfg.enTransMode = SpiTransFullDuplex;
    cfg.enCommAutoSuspendEn = Disable;
    cfg.enModeFaultErrorDetectEn = Disable;
    cfg.enParitySelfDetectEn = Disable;
    cfg.enParityEn = Disable;
    cfg.enParity = SpiParityEven;
    cfg.enMasterSlaveMode = SpiModeMaster;
    cfg.stcDelayConfig.enSsSetupDelayOption = SpiSsSetupDelayCustomValue;
    cfg.stcDelayConfig.enSsSetupDelayTime = SpiSsSetupDelaySck1;
    cfg.stcDelayConfig.enSsHoldDelayOption = SpiSsHoldDelayCustomValue;
    cfg.stcDelayConfig.enSsHoldDelayTime = SpiSsHoldDelaySck1;
    cfg.stcDelayConfig.enSsIntervalTimeOption = SpiSsIntervalCustomValue;
    cfg.stcDelayConfig.enSsIntervalTime = SpiSsIntervalSck6PlusPck2;
    cfg.stcSsConfig.enSsValidBit = SpiSsValidChannel0;
    cfg.stcSsConfig.enSs0Polarity = SpiSsLowValid;

    SPI_Init(M4_SPI2, &cfg);
    SPI_Cmd(M4_SPI2, Enable);

    spi_initialized = 1;
}

struct spi_config
spi_setup(uint32_t bus, uint8_t mode, uint32_t rate)
{
    if (bus != 0)
        shutdown("Invalid spi bus");
    spi_hw_init();
    return (struct spi_config){ .spi = (void*)M4_SPI2, .spi_cr1 = mode };
}

void
spi_prepare(struct spi_config config)
{
    // Single bus, always prepared after init
}

void
spi_transfer(struct spi_config config, uint8_t receive_data,
             uint8_t len, uint8_t *data)
{
    M4_SPI_TypeDef *spi = (M4_SPI_TypeDef *)config.spi;
    while (len--) {
        SPI_SendData8(spi, *data);
        while (SPI_GetFlag(spi, SpiFlagSendBufferEmpty) == Reset)
            ;
        // Wait for transfer complete
        while (SPI_GetFlag(spi, SpiFlagSpiIdle) == Reset)
            ;
        uint8_t rdata = SPI_ReceiveData8(spi);
        if (receive_data)
            *data = rdata;
        data++;
    }
}
