# Profile for Anycubic Kobra Neo
from . import DisplayProfile, DisplayCapabilities, Rotation

PROFILE = DisplayProfile(
    version=1,
    controller="st7789v",
    spi_bus="spi2",
    cs_pin="PB1",
    dc_pin="PB0",
    reset_pin=None,
    backlight_pin="PC0",
    encoder_pins="PB10,PB3",
    click_pin="PB4",
    buzzer_pin="PB7",
    touch_cs_pin=None,
    touch_irq_pin=None,
    width=240,
    height=320,
    rotation=Rotation.ROTATE_270,
    color_order="RGB",
    pixel_format="RGB565",
    spi_frequency=40000000,
    touch_controller=None,
    capabilities=DisplayCapabilities(
        has_backlight=True,
        has_buzzer=True,
        has_encoder=True,
        has_touch=False
    )
)
