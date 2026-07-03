# Profile definitions for generic display framework
#
# Copyright (C) 2024
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class Rotation(Enum):
    ROTATE_0 = 0
    ROTATE_90 = 90
    ROTATE_180 = 180
    ROTATE_270 = 270

@dataclass(frozen=True)
class DisplayCapabilities:
    backlight: bool
    buzzer: bool
    encoder: bool
    touch: bool

@dataclass(frozen=True)
class PinConfiguration:
    spi_bus: str
    cs_pin: str
    dc_pin: str
    reset_pin: Optional[str]
    backlight_pin: Optional[str]
    encoder_pins: Optional[str]
    click_pin: Optional[str]
    buzzer_pin: Optional[str]
    touch_cs_pin: Optional[str]
    touch_irq_pin: Optional[str]

@dataclass(frozen=True)
class DisplayProfile:
    name: str
    version: int
    controller: str
    width: int
    height: int
    rotation: Rotation
    color_order: str
    pixel_format: str
    spi_frequency: Optional[int]
    touch_controller: Optional[str]
    capabilities: DisplayCapabilities
    pins: PinConfiguration

# Load available profiles
from . import kobra_neo

PROFILES = {
    "kobra_neo": kobra_neo.PROFILE,
}

def get_profile(name):
    return PROFILES.get(name)
