import unittest
from .spi_tft import (SpiTftConfigWrapper, register_display_controller,
                      _DISPLAY_CONTROLLERS, TestPhase, SpiTftDisplay)
from . import spi_tft
from .profiles import (DisplayProfile, DisplayCapabilities, PinConfiguration,
                       Rotation)

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
            backlight_pin=None, encoder_pins="PA3,PA4", click_pin="PA5",
            buzzer_pin=None, touch_cs_pin=None, touch_irq_pin=None
        )
        profile = DisplayProfile(
            name="mock_profile", version=1,
            controller="mock", width=240, height=240,
            rotation=Rotation.ROTATE_0, color_order="RGB",
            pixel_format="RGB565", spi_frequency=20000000,
            touch_controller=None, capabilities=capabilities, pins=pins
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
            backlight_pin="PB1", encoder_pins="PA3,PA4", click_pin="PA5",
            buzzer_pin="PB2", touch_cs_pin="PC1", touch_irq_pin="PC2"
        )
        profile = DisplayProfile(
            name="mock_profile", version=1,
            controller="mock", width=240, height=240,
            rotation=Rotation.ROTATE_0, color_order="RGB",
            pixel_format="RGB565", spi_frequency=40000000,
            touch_controller="xpt2046", capabilities=capabilities, pins=pins
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

class MockPrinter:
    def __init__(self):
        self.objects = {}
        self.events = []
    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)
    def register_event_handler(self, event, callback):
        self.events.append((event, callback))

class MockReactor:
    def __init__(self):
        self.NOW = 1000.0
        self.NEVER = 999999.0
        self.timers = {}
        self.timer_id = 1
    def register_timer(self, callback, next_time):
        tid = self.timer_id
        self.timers[tid] = (callback, next_time)
        self.timer_id += 1
        return tid
    def update_timer(self, timer_id, next_time):
        if timer_id in self.timers:
            cb, _ = self.timers[timer_id]
            self.timers[timer_id] = (cb, next_time)

class MockGCode:
    def __init__(self):
        self.commands = {}
        self.responses = []
    def register_command(self, cmd, callback, desc=None):
        self.commands[cmd] = callback
    def respond_info(self, msg):
        self.responses.append(msg)

class TestSpiTftDiagnostics(unittest.TestCase):
    def setUp(self):
        self.printer = MockPrinter()
        self.reactor = MockReactor()
        self.gcode = MockGCode()

        self.printer.objects['gcode'] = self.gcode
        self.printer.objects['pins'] = None

        # Fake config for SpiTftDisplay
        config = MockConfig({'profile': 'mock_profile'})
        config.getprinter = lambda: self.printer
        config.get_printer = lambda: self.printer
        config.getboolean = lambda x, y: y
        self.printer.get_reactor = lambda: self.reactor

        # Setup a dummy profile in profiles
        capabilities = DisplayCapabilities(
            backlight=False, buzzer=False, encoder=False, touch=False
        )
        pins = PinConfiguration(
            spi_bus="spi1", cs_pin="PA1", dc_pin="PA2", reset_pin=None,
            backlight_pin=None, encoder_pins=None, click_pin=None,
            buzzer_pin=None, touch_cs_pin=None, touch_irq_pin=None
        )
        profile = DisplayProfile(
            name="mock_profile", version=1,
            controller="mock_ctrl", width=240, height=240,
            rotation=Rotation.ROTATE_0, color_order="RGB",
            pixel_format="RGB565", spi_frequency=20000000,
            touch_controller=None, capabilities=capabilities, pins=pins
        )

        @register_display_controller("mock_ctrl")
        class MockController:
            def __init__(self, config):
                self.cleared = False
            def clear(self):
                self.cleared = True
            def write_text(self, x, y, text):
                pass
            def flush(self):
                pass

        # Inject profile
        spi_tft.profiles.PROFILES["mock_profile"] = profile

        self.display = SpiTftDisplay(config)

    def test_testphase_transitions(self):
        self.assertEqual(self.display.test_state, TestPhase.IDLE)
        self.display.cmd_DISPLAY_TEST(self.gcode)
        self.assertEqual(self.display.test_state, TestPhase.RED)

        # Fire timer event
        cb, next_t = self.reactor.timers[self.display.test_timer]
        next_t = cb(next_t)
        self.assertEqual(self.display.test_state, TestPhase.GREEN)

    def test_cancel(self):
        self.display.cmd_DISPLAY_TEST(self.gcode)
        self.display.cmd_DISPLAY_TEST_CANCEL(self.gcode)
        self.assertEqual(self.display.test_state, TestPhase.RESTORE)

if __name__ == '__main__':
    unittest.main()
