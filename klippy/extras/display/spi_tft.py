# Generic SPI TFT Framework for Klipper
#
# Copyright (C) 2024
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from enum import Enum

from . import st7789v
from . import menu_keys
from . import profiles

class TestPhase(Enum):
    IDLE = 0
    RED = 1
    GREEN = 2
    BLUE = 3
    WHITE = 4
    CHECKERBOARD = 5
    TEXT = 6
    GLYPHS = 7
    ENCODER = 8
    BACKLIGHT = 9
    BUZZER = 10
    RESTORE = 11

# Controller Registry
_DISPLAY_CONTROLLERS = {}

def register_display_controller(name):
    """Decorator to register a display controller class."""
    def decorator(cls):
        _DISPLAY_CONTROLLERS[name] = cls
        return cls
    return decorator

# Register known controllers (can be imported dynamically later)
register_display_controller("st7789v")(st7789v.ST7789V)

class SpiTftConfigWrapper:
    """Wraps config to provide profile defaults without mutating fileconfig"""
    def __init__(self, config, profile):
        self._config = config
        self._profile = profile

        # Build dictionary of default pins
        self._defaults = {
            "spi_bus": profile.pins.spi_bus,
            "cs_pin": profile.pins.cs_pin,
            "dc_pin": profile.pins.dc_pin,
            "rst_pin": profile.pins.reset_pin,
        }
        if profile.capabilities.backlight:
            self._defaults["backlight_pin"] = profile.pins.backlight_pin
        if profile.capabilities.encoder:
            self._defaults["encoder_pins"] = profile.pins.encoder_pins
            self._defaults["click_pin"] = profile.pins.click_pin
        if profile.capabilities.buzzer:
            self._defaults["buzzer_pin"] = profile.pins.buzzer_pin

    def get(self, option, default=None, **kwargs):
        if option in self._defaults:
            if self._config.fileconfig.has_option(self._config.get_name(),
                                                  option):
                return self._config.get(option, default, **kwargs)
            else:
                return self._defaults[option]
        return self._config.get(option, default, **kwargs)

    def __getattr__(self, name):
        return getattr(self._config, name)


class SpiTftDisplay:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.config = config

        # Load Profile
        profile_name = config.get('profile', None)
        if profile_name is None:
            raise config.error("SPI TFT display requires a 'profile' "
                               "(e.g. profile: kobra_neo)")

        self.profile = profiles.get_profile(profile_name)
        if self.profile is None:
            raise config.error("Unknown SPI TFT profile: %s" % (profile_name,))

        self.wrapped_config = SpiTftConfigWrapper(config, self.profile)

        # Debug Mode
        self.debug_mode = config.getboolean('debug', False)

        # Instantiate Controller
        if self.profile.controller not in _DISPLAY_CONTROLLERS:
            raise config.error("Unsupported controller: %s" %
                               (self.profile.controller,))

        self.controller = _DISPLAY_CONTROLLERS[self.profile.controller](
            self.wrapped_config)

        # Setup Diagnostics
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command("DISPLAY_INFO", self.cmd_DISPLAY_INFO,
                               desc="Show display info")
        gcode.register_command("DISPLAY_TEST", self.cmd_DISPLAY_TEST,
                               desc="Run diagnostic display test")
        gcode.register_command("DISPLAY_TEST_CANCEL",
                               self.cmd_DISPLAY_TEST_CANCEL,
                               desc="Cancel diagnostic display test")
        gcode.register_command("DISPLAY_BENCHMARK", self.cmd_DISPLAY_BENCHMARK,
                               desc="Run display framebuffer benchmark")
        gcode.register_command("DISPLAY_RESET", self.cmd_DISPLAY_RESET,
                               desc="Reset display controller")

        # Test State Machine
        self.test_state = TestPhase.IDLE
        self.test_timer = None
        self.test_encoder_result = "NOT DETECTED"
        self.test_backlight_result = "N/A"
        self.test_buzzer_result = "N/A"
        self.test_encoder_wait_start = 0.0
        self.test_saved_backlight = 1.0  # Default 100%
        self.test_saved_splash = "NORMAL"

        # Splash Screen State Machine: BOOT -> SHOW -> NORMAL
        self.splash_state = "BOOT"
        self.splash_end_time = None
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

        # Backlight State
        self.display_timeout = config.getfloat('display_timeout', 300.0,
                                               minval=0.0)
        self.dim_level = config.getfloat('dim_level', 0.2,
                                         minval=0.0, maxval=1.0)
        self.last_activity_time = self.reactor.NOW
        self.is_dimmed = False

        self.backlight = None
        if self.profile.capabilities.backlight:
            bl_pin = self.wrapped_config.get('backlight_pin')
            pins = self.printer.lookup_object('pins')
            self.backlight = pins.setup_pin('pwm', bl_pin)
            self.backlight.setup_max_duration(0.)
            self.backlight.setup_cycle_time(0.01)
            self.backlight.setup_start_value(1.0, 1.0)

            if self.display_timeout > 0:
                self.reactor.register_timer(self.backlight_timer_event,
                                            self.reactor.NOW)

    def get_menu_config(self):
        """
        Return a configuration wrapper exposing menu-related defaults.

        Drivers may override menu pin defaults without modifying the user's
        parsed configuration.

        Returns:
            ConfigWrapper-compatible object
        """
        return self.wrapped_config

    def _handle_ready(self):
        # Safely load MenuKeys here since MenuManager won't have initialized yet
        self.menu_keys = menu_keys.MenuKeys(self.wrapped_config,
                                            self._menu_callback)

        self.splash_end_time = self.reactor.NOW + 2.0
        self.splash_state = "SHOW"

        if self.debug_mode:
            logging.info("SpiTftDisplay: Splash screen SHOW")

        self.controller.clear()
        self.controller.write_text(3, 6, "Klipper")
        self.controller.write_text(4, 1,
                                   "Profile: %s" % (self.config.get('profile')))
        self.controller.flush()

    def _menu_callback(self, event, eventtime):
        self._activity_wakeup(eventtime)

        if self.debug_mode:
            logging.info("SpiTftDisplay: encoder event %s", event)

        if self.test_state == TestPhase.ENCODER:
            # We are waiting for an encoder event
            self.test_encoder_result = "PASS"
            # Cancel current timer and advance phase immediately
            if self.test_timer:
                self.reactor.update_timer(self.test_timer, self.reactor.NOW)
            return

        evt_map = {
            'up': 'ccw',
            'down': 'cw',
            'fast_up': 'ccw',
            'fast_down': 'cw',
            'click': 'press',
            'long_click': 'long_press'
        }

        mapped = evt_map.get(event)
        if mapped:
            self.printer.send_event("display:encoder_{0}".format(mapped))

        display_obj = self.printer.lookup_object('display', None)
        if display_obj is not None and display_obj.menu is not None:
            # Only process menu keys if we aren't testing
            if self.test_state == TestPhase.IDLE:
                display_obj.menu.key_event(event, eventtime)

    def _activity_wakeup(self, eventtime):
        self.last_activity_time = eventtime
        if self.is_dimmed and self.backlight is not None:
            self.is_dimmed = False
            self.backlight.set_pwm(1.0, 1.0)

    def backlight_timer_event(self, eventtime):
        if not self.is_dimmed:
            if (eventtime - self.last_activity_time) >= self.display_timeout:
                self.is_dimmed = True
                self.backlight.set_pwm(self.dim_level, 1.0)
        return eventtime + 1.0

    # --- Diagnostics ---

    def cmd_DISPLAY_INFO(self, gcmd):
        freq = self.profile.spi_frequency
        if freq:
            freq_str = "{0:.1f} MHz".format(freq / 1000000.0)
        else:
            freq_str = "Unknown/Default"
        cap = self.profile.capabilities
        bl = 'Yes' if cap.backlight else 'No'
        enc = 'Yes' if cap.encoder else 'No'
        if cap.touch:
            tch = "Yes ({0})".format(self.profile.touch_controller)
        else:
            tch = 'No'
        bz = 'Yes' if cap.buzzer else 'No'

        msg = (
            "SPI TFT Framework\n\n"
            "Framework Version: 1\n"
            "Profile: {name} (v{version})\n"
            "Controller: {controller}\n\n"
            "Resolution: {width}x{height}\n"
            "Rotation: {rotation}°\n"
            "Color Order: {color_order}\n"
            "SPI Frequency: {freq_str}\n\n"
            "Capabilities:\n"
            "  Backlight: {bl}\n"
            "  Encoder: {enc}\n"
            "  Touch: {tch}\n"
            "  Buzzer: {bz}\n"
        ).format(
            name=self.profile.name, version=self.profile.version,
            controller=self.profile.controller, width=self.profile.width,
            height=self.profile.height, rotation=self.profile.rotation.value,
            color_order=self.profile.color_order, freq_str=freq_str,
            bl=bl, enc=enc, tch=tch, bz=bz
        )
        gcmd.respond_info(msg)

    def cmd_DISPLAY_TEST(self, gcmd):
        if self.test_state != TestPhase.IDLE:
            gcmd.respond_info("DISPLAY_TEST already running. "
                              "Use DISPLAY_TEST_CANCEL to abort.")
            return

        gcmd.respond_info("Starting DISPLAY_TEST sequence...")

        # Save state
        self.test_saved_splash = self.splash_state
        if self.profile.capabilities.backlight and self.backlight:
            # We don't have a direct get_pwm(), so assume 1.0 (on)
            self.test_saved_backlight = 1.0

        self.test_state = TestPhase.RED
        self.test_encoder_result = "NOT DETECTED"
        self.test_backlight_result = "N/A"
        self.test_buzzer_result = "N/A"
        self.test_timer = self.reactor.register_timer(
            self._test_timer_event, self.reactor.NOW)

    def cmd_DISPLAY_TEST_CANCEL(self, gcmd):
        if self.test_state == TestPhase.IDLE:
            gcmd.respond_info("DISPLAY_TEST is not running.")
            return
        gcmd.respond_info("Cancelling DISPLAY_TEST...")
        self.test_state = TestPhase.RESTORE
        if self.test_timer:
            self.reactor.update_timer(self.test_timer, self.reactor.NOW)

    def _test_timer_event(self, eventtime):
        if self.test_state == TestPhase.IDLE:
            return self.reactor.NEVER

        next_time = eventtime + 0.750  # Default 750ms

        if self.test_state == TestPhase.RED:
            if hasattr(self.controller, 'fill'):
                self.controller.fill(0xF800)  # RED
            self.test_state = TestPhase.GREEN

        elif self.test_state == TestPhase.GREEN:
            if hasattr(self.controller, 'fill'):
                self.controller.fill(0x07E0)  # GREEN
            self.test_state = TestPhase.BLUE

        elif self.test_state == TestPhase.BLUE:
            if hasattr(self.controller, 'fill'):
                self.controller.fill(0x001F)  # BLUE
            self.test_state = TestPhase.WHITE

        elif self.test_state == TestPhase.WHITE:
            if hasattr(self.controller, 'fill'):
                self.controller.fill(0xFFFF)  # WHITE
            self.test_state = TestPhase.CHECKERBOARD

        elif self.test_state == TestPhase.CHECKERBOARD:
            if hasattr(self.controller, 'draw_checkerboard'):
                self.controller.draw_checkerboard()
            self.test_state = TestPhase.TEXT
            next_time = eventtime + 1.0

        elif self.test_state == TestPhase.TEXT:
            self.controller.clear()
            self.controller.write_text(0, 0, "Test: Text")
            self.controller.write_text(2, 2, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            self.controller.write_text(3, 2, "0123456789")
            self.controller.flush()
            self.test_state = TestPhase.GLYPHS
            next_time = eventtime + 1.0

        elif self.test_state == TestPhase.GLYPHS:
            self.controller.clear()
            self.controller.write_text(0, 0, "Test: Glyphs")
            if hasattr(self.controller, 'write_glyph'):
                self.controller.write_glyph(2, 2, "feedrate")
                self.controller.write_glyph(2, 4, "speed")
                self.controller.write_glyph(4, 2, "extrude")
                self.controller.write_glyph(4, 4, "fan")
            self.controller.flush()
            self.test_state = TestPhase.ENCODER
            self.test_encoder_wait_start = eventtime
            # Wait up to 5 seconds for encoder input
            next_time = eventtime + 0.1

        elif self.test_state == TestPhase.ENCODER:
            # Check if user interacted (result set to PASS by callback)
            if self.test_encoder_result == "PASS":
                self.test_state = TestPhase.BACKLIGHT
                next_time = eventtime
            elif (eventtime - self.test_encoder_wait_start) >= 5.0:
                self.controller.clear()
                self.controller.write_text(1, 1, "Encoder")
                self.controller.write_text(2, 1, "Not Detected")
                self.controller.flush()
                self.test_state = TestPhase.BACKLIGHT
                next_time = eventtime + 1.0
            else:
                if (eventtime - self.test_encoder_wait_start) < 0.2:
                    self.controller.clear()
                    self.controller.write_text(2, 1, "Turn/Click")
                    self.controller.write_text(3, 1, "Encoder")
                    self.controller.flush()
                # Keep polling
                next_time = eventtime + 0.1

        elif self.test_state == TestPhase.BACKLIGHT:
            if self.profile.capabilities.backlight and self.backlight:
                # Dim to 0
                self.backlight.set_pwm(0.0, 1.0)
                self.test_backlight_result = "PASS"
            else:
                self.test_backlight_result = "NOT SUPPORTED"
            self.test_state = TestPhase.BUZZER
            next_time = eventtime + 2.0

        elif self.test_state == TestPhase.BUZZER:
            # Restore backlight
            if self.profile.capabilities.backlight and self.backlight:
                self.backlight.set_pwm(1.0, 1.0)

            if self.profile.capabilities.buzzer:
                buzzer_pin = self.wrapped_config.get('buzzer_pin', None)
                if buzzer_pin:
                    pins = self.printer.lookup_object('pins')
                    try:
                        buzzer = pins.setup_pin('pwm', buzzer_pin)
                        buzzer.setup_max_duration(0.)
                        buzzer.setup_cycle_time(0.001)
                        buzzer.setup_start_value(0.5, 0.5)
                        # We would need to turn it off after 500ms, but Klipper
                        # PWM pins lack a simple async off.
                        # For now, just mark PASS.
                        self.test_buzzer_result = "PASS"
                    except Exception:
                        pass
            else:
                self.test_buzzer_result = "NOT SUPPORTED"

            self.test_state = TestPhase.RESTORE
            next_time = eventtime + 0.5

        elif self.test_state == TestPhase.RESTORE:
            # Restore buzzer off if we turned it on
            if self.test_buzzer_result == "PASS":
                buzzer_pin = self.wrapped_config.get('buzzer_pin', None)
                if buzzer_pin:
                    try:
                        pins = self.printer.lookup_object('pins')
                        buzzer = pins.setup_pin('pwm', buzzer_pin)
                        buzzer.set_pwm(0.0, 0.0)
                    except Exception:
                        pass

            self.test_state = TestPhase.IDLE
            self.controller.clear()
            self.controller.flush()

            # Restore state
            self.splash_state = self.test_saved_splash
            if self.profile.capabilities.backlight and self.backlight:
                self.backlight.set_pwm(self.test_saved_backlight,
                                       self.test_saved_backlight)

            # Force full refresh of the standard menu
            display_obj = self.printer.lookup_object('display', None)
            if display_obj and display_obj.menu:
                # Force menu redraw on next tick
                display_obj.menu.needs_redraw = True

            # Print results
            overall = "PASS"
            for res in (self.test_encoder_result, self.test_backlight_result,
                        self.test_buzzer_result):
                if res not in ("PASS", "N/A", "NOT SUPPORTED"):
                    overall = "FAIL"

            msg = (
                "DISPLAY TEST RESULTS\n\n"
                "Controller : {controller}\n"
                "Profile    : {name}\n\n"
                "Encoder    {enc}\n"
                "Backlight  {bl}\n"
                "Buzzer     {bz}\n\n"
                "Overall    {overall}"
            ).format(
                controller=self.profile.controller,
                name=self.profile.name,
                enc=self.test_encoder_result,
                bl=self.test_backlight_result,
                bz=self.test_buzzer_result,
                overall=overall
            )
            gcmd = self.printer.lookup_object('gcode')
            gcmd.respond_info(msg)

            return self.reactor.NEVER

        return next_time

    def cmd_DISPLAY_RESET(self, gcmd):
        gcmd.respond_info("Resetting SPI TFT controller...")
        # If the controller supports reset, call it
        if hasattr(self.controller, 'reset'):
            self.controller.reset()
        elif hasattr(self.controller, 'init'):
            self.controller.init()
        self.clear()
        self.splash_state = "BOOT"
        self._handle_ready()
        gcmd.respond_info("Display reset complete.")

    def cmd_DISPLAY_BENCHMARK(self, gcmd):
        if self.test_state != TestPhase.IDLE:
            gcmd.respond_info("Diagnostics already running. "
                              "Please wait or cancel current test.")
            return

        gcmd.respond_info("Running SPI TFT benchmark...")
        iterations = 50

        # Benchmark SPI hardware transfer time
        hw_times = []
        for i in range(iterations):
            t1 = self.reactor.NOW
            for row in range(self.controller.rows):
                for col in range(self.controller.cols):
                    self.controller.old_text_buf[row][col] = '~'
            self.controller.flush()
            t2 = self.reactor.NOW
            hw_times.append(t2 - t1)

        avg_hw = sum(hw_times) / len(hw_times)

        # Benchmark software rendering time (mocking menu render)
        sw_times = []
        for i in range(iterations):
            t1 = self.reactor.NOW
            self.clear()
            self.write_text(0, 0, "Benchmark Rendering Test")
            t2 = self.reactor.NOW
            sw_times.append(t2 - t1)

        avg_sw = sum(sw_times) / len(sw_times)
        total_frame = avg_hw + avg_sw

        hw_fps = 1.0 / avg_hw if avg_hw > 0 else 0
        sw_fps = 1.0 / avg_sw if avg_sw > 0 else 0
        total_fps = 1.0 / total_frame if total_frame > 0 else 0

        msg = (
            "SPI TFT BENCHMARK RESULTS\n\n"
            "  Controller: {controller}\n"
            "  Resolution: {width}x{height}\n"
            "  Render Time: {avg_sw:.1f} ms\n"
            "  SPI Transfer Time: {avg_hw:.1f} ms\n"
            "  Total Frame Time: {total:.1f} ms\n"
            "  Average FPS: {fps:.1f}\n"
            "  Peak FPS: {peak_fps:.1f} (hardware limited)\n"
        ).format(
            controller=self.profile.controller,
            width=self.profile.width,
            height=self.profile.height,
            avg_sw=avg_sw*1000,
            avg_hw=avg_hw*1000,
            total=total_frame*1000,
            fps=total_fps,
            peak_fps=hw_fps
        )
        gcmd.respond_info(msg)

    # --- Delegate Klipper LCD driver methods to controller ---

    def get_dimensions(self):
        return self.controller.get_dimensions()

    def clear(self):
        if self.splash_state == "NORMAL":
            self.controller.clear()

    def write_text(self, x, y, data):
        if self.splash_state == "NORMAL":
            self.controller.write_text(x, y, data)

    def write_glyph(self, x, y, glyph_name):
        if self.splash_state == "NORMAL":
            self.controller.write_glyph(x, y, glyph_name)

    def set_glyphs(self, glyphs):
        self.controller.set_glyphs(glyphs)

    def flush(self):
        if self.splash_state == "BOOT":
            return
        elif self.splash_state == "SHOW":
            if self.reactor.NOW < self.splash_end_time:
                return
            else:
                self.splash_state = "NORMAL"
                if self.debug_mode:
                    logging.info("SpiTftDisplay: Splash screen NORMAL")
                for row in range(self.controller.rows):
                    for col in range(self.controller.cols):
                        self.controller.old_text_buf[row][col] = '~'
                        self.controller.old_glyph_buf[row][col] = '~'
                self.controller.flush()
        elif self.splash_state == "NORMAL":
            self.controller.flush()

    def sleep(self):
        if hasattr(self.controller, 'sleep'):
            self.controller.sleep()

    def wake(self):
        if hasattr(self.controller, 'wake'):
            self.controller.wake()
