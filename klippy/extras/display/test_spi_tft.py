import unittest
from .spi_tft import SpiTftConfigWrapper, register_display_controller, _DISPLAY_CONTROLLERS
from .profiles import DisplayProfile, DisplayCapabilities, PinConfiguration, Rotation

class MockConfig:
    def __init__(self, items):
        self.items = items
        self.error = Exception
        self.fileconfig = self
        self.name = "display"

    def get_name(self):
        return self.name

    def has_option(self, section, option):
        return option in self.items

    def get(self, option, default=None):
        return self.items.get(option, default)

class TestSpiTftFramework(unittest.TestCase):
    def test_profile_capabilities(self):
        capabilities = DisplayCapabilities(
            backlight=False, buzzer=False, encoder=True, touch=False
        )
        pins = PinConfiguration(
            spi_bus="spi1", cs_pin="PA1", dc_pin="PA2", reset_pin=None,
            backlight_pin=None, encoder_pins="PA3,PA4", click_pin="PA5", buzzer_pin=None,
            touch_cs_pin=None, touch_irq_pin=None
        )
        profile = DisplayProfile(
            name="mock_profile", version=1,
            controller="mock", width=240, height=240, rotation=Rotation.ROTATE_0, color_order="RGB", pixel_format="RGB565",
            spi_frequency=20000000, touch_controller=None, capabilities=capabilities, pins=pins
        )
        self.assertFalse(profile.capabilities.backlight)
        self.assertFalse(profile.capabilities.buzzer)
        self.assertTrue(profile.capabilities.encoder)

    def test_config_wrapper(self):
        capabilities = DisplayCapabilities(
            backlight=True, buzzer=True, encoder=True, touch=True
        )
        pins = PinConfiguration(
            spi_bus="spi1", cs_pin="PA1", dc_pin="PA2", reset_pin=None,
            backlight_pin="PB1", encoder_pins="PA3,PA4", click_pin="PA5", buzzer_pin="PB2",
            touch_cs_pin="PC1", touch_irq_pin="PC2"
        )
        profile = DisplayProfile(
            name="mock_profile", version=1,
            controller="mock", width=240, height=240, rotation=Rotation.ROTATE_0, color_order="RGB", pixel_format="RGB565",
            spi_frequency=40000000, touch_controller="xpt2046", capabilities=capabilities, pins=pins
        )
        
        # User provides no overrides
        config = MockConfig({})
        wrapper = SpiTftConfigWrapper(config, profile)
        self.assertEqual(wrapper.get("cs_pin"), "PA1")
        self.assertEqual(wrapper.get("encoder_pins"), "PA3,PA4")
        
        # User provides overrides
        config_override = MockConfig({"cs_pin": "PC13"})
        wrapper_override = SpiTftConfigWrapper(config_override, profile)
        self.assertEqual(wrapper_override.get("cs_pin"), "PC13")
        self.assertEqual(wrapper_override.get("encoder_pins"), "PA3,PA4")

    def test_controller_registry(self):
        @register_display_controller("mock_ctrl")
        class MockController:
            pass
            
        self.assertIn("mock_ctrl", _DISPLAY_CONTROLLERS)
        self.assertEqual(_DISPLAY_CONTROLLERS["mock_ctrl"], MockController)

if __name__ == '__main__':
    unittest.main()
