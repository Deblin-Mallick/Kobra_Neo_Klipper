# Support for ST7789V 320x240 color TFT displays (SPI)
# Used on Anycubic Kobra Neo and similar printers
#
# Copyright (C) 2024
# This file may be distributed under the terms of the GNU GPLv3 license.

from .. import bus
from . import font8x14
from .uc1701 import SPI4wire, ResetHelper

BACKGROUND_PRIORITY_CLOCK = 0x7fffffff00000000

TextGlyphs = { 'right_arrow': b'\x1a', 'degrees': b'\xf8' }

# RGB565 colors
COLOR_BLACK = 0x0000
COLOR_WHITE = 0xFFFF
COLOR_GREEN = 0x07E0
COLOR_YELLOW = 0xFFE0
COLOR_CYAN = 0x07FF
COLOR_RED = 0xF800
COLOR_BLUE = 0x001F
COLOR_ORANGE = 0xFD20


class ST7789V:
    """320x240 color TFT via SPI with DC pin. Renders a 20x8 character grid
    using a 2x-scaled 8x14 VGA font."""
    def __init__(self, config):
        self.printer = config.get_printer()
        # 4-wire SPI: cs_pin + dc_pin (plus shared sclk/mosi from spi_bus)
        self.io = SPI4wire(config, "dc_pin")
        self.reset = ResetHelper(config.get("rst_pin", None), self.io.spi)
        # Optional backlight; many ST7789V breakouts have an always-on BL,
        # but Anycubic boards wire it to a GPIO. Use [output_pin] in cfg if
        # you'd rather drive it from a separate section.
        bl_pin = config.get("backlight_pin", None)
        self.mcu_bl = None
        if bl_pin is not None:
            self.mcu_bl = bus.MCU_bus_digital_out(
                self.io.spi.get_mcu(), bl_pin,
                self.io.spi.get_command_queue())
        # 20 cols x 8 rows of 16x28 px chars fits 320x240 with 16px slack
        self.cols = 20
        self.rows = 8
        self.char_w = 16
        self.char_h = 28
        self.text_buf = [[' '] * self.cols for _ in range(self.rows)]
        self.old_text_buf = [['~'] * self.cols for _ in range(self.rows)]
        self.icons = {}
        self.font = font8x14.VGA_FONT

    def _delay(self, seconds):
        # Yield to the reactor instead of blocking with time.sleep().
        reactor = self.printer.get_reactor()
        reactor.pause(reactor.monotonic() + seconds)

    def init(self):
        self.reset.init()
        if self.mcu_bl is not None:
            self.mcu_bl.update_digital_out(
                1, reqclock=BACKGROUND_PRIORITY_CLOCK)
        send = self.io.send
        # Sleep out
        send([0x11])
        self._delay(0.12)
        # MADCTL: landscape MY|MV|ML = 0xB0
        send([0x36]); send([0xB0], is_data=True)
        # Pixel format: 16-bit/pixel
        send([0x3A]); send([0x05], is_data=True)
        # Frame rate / porch control
        send([0xB2]); send([0x05, 0x05, 0x00, 0x33, 0x33], is_data=True)
        send([0xB7]); send([0x35], is_data=True)
        # Power control
        send([0xBB]); send([0x28], is_data=True)
        send([0xC0]); send([0x2C], is_data=True)
        send([0xC2]); send([0x01], is_data=True)
        send([0xC3]); send([0x0B], is_data=True)
        send([0xC4]); send([0x20], is_data=True)
        send([0xC6]); send([0x0F], is_data=True)
        send([0xD0]); send([0xA4, 0xA1], is_data=True)
        # Gamma
        send([0xE0]); send([0xD0, 0x01, 0x08, 0x0F, 0x11, 0x2A, 0x36,
                            0x55, 0x44, 0x3A, 0x0B, 0x06, 0x11, 0x20],
                           is_data=True)
        send([0xE1]); send([0xD0, 0x02, 0x07, 0x0A, 0x0B, 0x18, 0x34,
                            0x43, 0x4A, 0x2B, 0x1B, 0x1C, 0x22, 0x1F],
                           is_data=True)
        # Display ON
        send([0x29])
        self._delay(0.05)
        # Clear to black and force a full repaint on next flush.
        self._fill_rect(0, 0, 320, 240, COLOR_BLACK)
        for row in range(self.rows):
            for col in range(self.cols):
                self.old_text_buf[row][col] = '~'

    def _set_window(self, x, y, w, h):
        xe = x + w - 1
        ye = y + h - 1
        send = self.io.send
        send([0x2A])
        send([x >> 8, x & 0xFF, xe >> 8, xe & 0xFF], is_data=True)
        send([0x2B])
        send([y >> 8, y & 0xFF, ye >> 8, ye & 0xFF], is_data=True)
        send([0x2C])

    def _fill_rect(self, x, y, w, h, color):
        self._set_window(x, y, w, h)
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF
        row_data = bytearray([hi, lo] * w)
        for _ in range(h):
            self.io.send(row_data, is_data=True)

    def _draw_char(self, col, row, ch, fg=COLOR_WHITE, bg=COLOR_BLACK):
        x = col * self.char_w
        y = row * self.char_h
        if x >= 320 or y >= 240:
            return
        c = ord(ch) if isinstance(ch, str) else ch
        if c > 127:
            c = ord('?')
        glyph = self.font[c]
        pixels = bytearray(self.char_w * self.char_h * 2)
        idx = 0
        fg_hi, fg_lo = (fg >> 8) & 0xFF, fg & 0xFF
        bg_hi, bg_lo = (bg >> 8) & 0xFF, bg & 0xFF
        for font_row in range(14):
            row_byte = glyph[font_row]
            scaled_row = bytearray(32)
            for bit in range(8):
                if row_byte & (0x80 >> bit):
                    scaled_row[bit*4]     = fg_hi
                    scaled_row[bit*4 + 1] = fg_lo
                    scaled_row[bit*4 + 2] = fg_hi
                    scaled_row[bit*4 + 3] = fg_lo
                else:
                    scaled_row[bit*4]     = bg_hi
                    scaled_row[bit*4 + 1] = bg_lo
                    scaled_row[bit*4 + 2] = bg_hi
                    scaled_row[bit*4 + 3] = bg_lo
            pixels[idx:idx + 32] = scaled_row
            idx += 32
            pixels[idx:idx + 32] = scaled_row
            idx += 32
        self._set_window(x, y, self.char_w, self.char_h)
        self.io.send(pixels, is_data=True)

    def flush(self):
        for row in range(self.rows):
            for col in range(self.cols):
                if self.text_buf[row][col] != self.old_text_buf[row][col]:
                    ch = self.text_buf[row][col]
                    fg = COLOR_CYAN if row == 0 else COLOR_WHITE
                    self._draw_char(col, row, ch, fg=fg, bg=COLOR_BLACK)
                    self.old_text_buf[row][col] = self.text_buf[row][col]

    def set_glyphs(self, glyphs):
        for glyph_name, glyph_data in glyphs.items():
            icon = glyph_data.get('icon16x16')
            if icon is not None:
                self.icons[glyph_name] = icon

    def write_text(self, x, y, data):
        if y >= self.rows:
            return
        for i, ch in enumerate(data):
            col = x + i
            if col >= self.cols:
                break
            if isinstance(ch, int):
                ch = chr(ch)
            self.text_buf[y][col] = ch

    def write_graphics(self, x, y, data):
        pass

    def write_glyph(self, x, y, glyph_name):
        text = TextGlyphs.get(glyph_name)
        if text is not None:
            self.write_text(x, y, text.decode('latin-1'))
            return len(text)
        return 0

    def clear(self):
        for row in range(self.rows):
            for col in range(self.cols):
                self.text_buf[row][col] = ' '

    def get_dimensions(self):
        return (self.cols, self.rows)
