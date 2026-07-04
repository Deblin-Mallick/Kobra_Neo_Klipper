# Support for ST7789V 320x240 color TFT displays (SPI)
# Used on Anycubic Kobra Neo and similar printers
#
# Copyright (C) 2024
# This file may be distributed under the terms of the GNU GPLv3 license.

import time
import logging

from .. import bus
from . import font8x14
from .uc1701 import SPI4wire, ResetHelper

BACKGROUND_PRIORITY_CLOCK = 0x7fffffff00000000

# Klipper SPI commands are encoded with a uint8_t length prefix, and the
# whole message envelope is capped near MESSAGE_MAX=64 bytes, so each
# spi_send() payload must be small. 32 leaves comfortable headroom.
SPI_CHUNK = 32

# Glyphs for code points beyond ASCII 0-127 that aren't in font8x14.VGA_FONT.
# Each entry is 14 rows of 8 pixels (MSB-leftmost), matching the VGA font.
EXTRA_GLYPHS = {
    0xF8: bytes([  # degrees (Klipper writes this for temperature labels)
        0b00000000, 0b00111000, 0b01000100, 0b01000100,
        0b00111000, 0b00000000, 0b00000000, 0b00000000,
        0b00000000, 0b00000000, 0b00000000, 0b00000000,
        0b00000000, 0b00000000,
    ]),
}

TextGlyphs = {
    'right_arrow': b'\x1a',
    'degrees': b'\xf8',
    # 'extruder': b'\xf9',
    # 'bed': b'\xfa',
    # 'bed_heat1': b'\xfb',
    # 'bed_heat2': b'\xfc',
    # 'fan1': b'\xfd',
    # 'fan2': b'\xfe',
    # 'feedrate': b'\xff',
}

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
        # but Anycubic boards wire it to a GPIO.
        # The framework handles it via PWM.
        self.mcu_bl = None
        # 20 cols x 8 rows of 16x28 px chars fits 320x240 with 16px slack
        self.cols = 20
        self.rows = 8
        self.char_w = 16
        self.char_h = 28
        self.text_buf = [[' '] * self.cols for _ in range(self.rows)]
        self.old_text_buf = [['~'] * self.cols for _ in range(self.rows)]
        self.glyph_buf = [[' '] * self.cols for _ in range(self.rows)]
        self.old_glyph_buf = [['~'] * self.cols for _ in range(self.rows)]
        self.cached_glyphs = {}
        self.icons = {}
        self.font = font8x14.VGA_FONT

    def _delay(self, seconds):
        # ST7789V requires ~120 ms after sleep-out and ~50 ms after display-on
        # for the chip's internal state machine. Klipper's reactor.pause() is
        # not allowed in display init context, so block briefly with sleep().
        time.sleep(seconds)

    def init(self):
        self.reset.init()
        send = self.io.send

        # 11h: Sleep Out - Turn off sleep mode. Requires 120ms delay afterward.
        send([0x11])
        self._delay(0.12)

        # 0xB0 = 10110000 -> MY=1 (Row Address Order), MV=1 (Row/Col Exchange),
        # ML=1 (Vertical Refresh Order)
        # Sets landscape orientation and drawing direction.
        send([0x36]); send([0xB0], is_data=True)

        # 3Ah: Interface Pixel Format (COLMOD)
        # 0x05 = 16 bits/pixel (RGB565)
        send([0x3A]); send([0x05], is_data=True)

        # B2h: Porch Setting
        # Controls front and back porch periods in normal, idle,
        # and partial modes.
        send([0xB2]); send([0x05, 0x05, 0x00, 0x33, 0x33], is_data=True)

        # B7h: Gate Control
        # 0x35 = VGH and VGL operating voltages.
        send([0xB7]); send([0x35], is_data=True)

        # BBh: VCOM Setting
        # 0x28 = VCOM voltage setting.
        send([0xBB]); send([0x28], is_data=True)

        # C0h: LCM Control
        # 0x2C = Default logic control settings.
        send([0xC0]); send([0x2C], is_data=True)

        # C2h: VDV and VRH Command Enable
        # 0x01 = User defined VRH and VDV.
        send([0xC2]); send([0x01], is_data=True)

        # C3h: VRH Set
        # 0x0B = VRH voltage.
        send([0xC3]); send([0x0B], is_data=True)

        # C4h: VDV Set
        # 0x20 = VDV voltage.
        send([0xC4]); send([0x20], is_data=True)

        # C6h: Frame Rate Control in Normal Mode
        # 0x0F = 60Hz frame rate.
        send([0xC6]); send([0x0F], is_data=True)

        # D0h: Power Control 1
        # 0xA4, 0xA1 = AVDD, AVCL, VDS, VGS voltages.
        send([0xD0]); send([0xA4, 0xA1], is_data=True)

        # E0h: Positive Voltage Gamma Control
        # Fine-tunes the grayscale voltages for the positive polarity.
        send([0xE0]); send([0xD0, 0x01, 0x08, 0x0F, 0x11, 0x2A, 0x36,
                            0x55, 0x44, 0x3A, 0x0B, 0x06, 0x11, 0x20],
                           is_data=True)

        # E1h: Negative Voltage Gamma Control
        # Fine-tunes the grayscale voltages for the negative polarity.
        send([0xE1]); send([0xD0, 0x02, 0x07, 0x0A, 0x0B, 0x18, 0x34,
                            0x43, 0x4A, 0x2B, 0x1B, 0x1C, 0x22, 0x1F],
                           is_data=True)

        # 29h: Display ON
        send([0x29])
        self._delay(0.05)

        # Clear to black and force a full repaint on next flush.
        self._fill_rect(0, 0, 320, 240, COLOR_BLACK)
        for row in range(self.rows):
            for col in range(self.cols):
                self.old_text_buf[row][col] = '~'
                self.old_glyph_buf[row][col] = '~'

    def _set_window(self, x, y, w, h):
        xe = x + w - 1
        ye = y + h - 1
        send = self.io.send
        send([0x2A])
        send([x >> 8, x & 0xFF, xe >> 8, xe & 0xFF], is_data=True)
        send([0x2B])
        send([y >> 8, y & 0xFF, ye >> 8, ye & 0xFF], is_data=True)
        send([0x2C])

    def _send_chunked(self, data):
        # Split data into SPI_CHUNK-sized writes (uint8_t length cap, ~56B
        # message envelope). Pre-slice once when reused across rows.
        send = self.io.send
        for i in range(0, len(data), SPI_CHUNK):
            send(data[i:i + SPI_CHUNK], is_data=True)

    def _fill_rect(self, x, y, w, h, color):
        self._set_window(x, y, w, h)
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF
        row_data = bytes([hi, lo] * w)
        chunks = [row_data[i:i + SPI_CHUNK]
                  for i in range(0, len(row_data), SPI_CHUNK)]
        send = self.io.send
        for _ in range(h):
            for chunk in chunks:
                send(chunk, is_data=True)

    def _draw_char(self, col, row, ch, fg=COLOR_WHITE, bg=COLOR_BLACK):
        x = col * self.char_w
        y = row * self.char_h
        if x >= 320 or y >= 240:
            return
        c = ord(ch) if isinstance(ch, str) else ch
        if c <= 127:
            glyph = self.font[c]
        else:
            glyph = EXTRA_GLYPHS.get(c)
            if glyph is None:
                glyph = self.font[ord('?')]
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
        self._send_chunked(pixels)

    # def _draw_glyph(self, col, row, glyph_names, fg, bg):
    #     pass
    def _draw_glyph(self,col,row,glyph_names, fg, bg):
            x = col * self.char_w
            y = row * self.char_h
            icon_pixel = self.icons.get(glyph_names)
            # icon_pixel = self.icons[glyph_names]
            if (icon_pixel is None) or (x >= 320 or y >= 240):
                return
            # logging.info(icon_pixel)

            pixels = bytearray(self.char_w * self.char_h * 2)
            idx = 0

            fg_hi, fg_lo = (fg >> 8) & 0xFF, fg & 0xFF
            bg_hi, bg_lo = (bg >> 8) & 0xFF, bg & 0xFF
            if len(icon_pixel) != 2:
                return
            left = icon_pixel[0]
            right = icon_pixel[1]

            for dst_row in range(self.char_h):

                src_row = (dst_row * 16) // self.char_h

                left_byte = left[src_row]
                right_byte = right[src_row]

                for bit in range(8):
                    if left_byte & (0x80 >> bit):
                        pixels[idx] = fg_hi
                        pixels[idx + 1] = fg_lo
                    else:
                        pixels[idx] = bg_hi
                        pixels[idx + 1] = bg_lo
                    idx += 2

                for bit in range(8):
                    if right_byte & (0x80 >> bit):
                        pixels[idx] = fg_hi
                        pixels[idx + 1] = fg_lo
                    else:
                        pixels[idx] = bg_hi
                        pixels[idx + 1] = bg_lo
                    idx += 2

            self._set_window(
                x ,y ,
                self.char_w, self.char_h,
            )
            self._send_chunked(pixels)

            return 1
    def flush(self):
        for row in range(self.rows):
            for col in range(self.cols):
                text_changed = self.text_buf[row][col] != self.old_text_buf[row][col]
                glyph_changed = self.glyph_buf[row][col] != self.old_glyph_buf[row][col]
                
                if text_changed or glyph_changed:
                    glyph_names = self.glyph_buf[row][col]
                    ch = self.text_buf[row][col]
                    
                    if glyph_names != ' ':
                        self._draw_glyph(col, row, glyph_names, fg=COLOR_WHITE, bg=COLOR_BLACK)
                    else:
                        fg = COLOR_CYAN if row == 0 else COLOR_WHITE
                        self._draw_char(col, row, ch, fg=fg, bg=COLOR_BLACK)
                        
                    self.old_text_buf[row][col] = ch
                    self.old_glyph_buf[row][col] = glyph_names

    # def cache_glyph(self, glyph_name, base_glyph_name, glyph_id):
    #     icon = self.icons.get(glyph_name)
    #     base_icon = self.icons.get(base_glyph_name)
    #     if icon is None or base_icon is None:
    #         return
    #     # all_bits = zip(icon[0], icon[1], base_icon[0], base_icon[1])
    #     # for i, (ic1, ic2, b1, b2) in enumerate(all_bits):
    #     #     x1, x2 = ic1 ^ b1, ic2 ^ b2
    #     #     pos = glyph_id*32 + i*2
    #     #     self.glyph_framebuffer[pos:pos+2] = [x1, x2]
    #     #     self.all_framebuffers[1][1][pos:pos+2] = [x1 ^ 1, x2 ^ 1]
    #     self.cached_glyphs[glyph_name] = (base_glyph_name, (0, glyph_id*2))

    def set_glyphs(self, glyphs):
        for glyph_name, glyph_data in glyphs.items():
            icon = glyph_data.get('icon16x16')
            if icon is not None:
                self.icons[glyph_name] = icon
        # Setup animated glyphs
        # self.cache_glyph('fan2', 'fan1', 0)
        # self.cache_glyph('bed_heat2', 'bed_heat1', 1)

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
            self.glyph_buf[y][col] = ' '

    def write_graphics(self, x, y, data):
        pass

    def write_glyph(self, x, y, glyph_name):

        if glyph_name in TextGlyphs:
            text = TextGlyphs.get(glyph_name)
            self.write_text(x, y, text.decode('latin-1'))
            # self.write_text(x, y, text)
            return len(text)
        if glyph_name in self.icons:
            # logging.info("glyph_name Worked")
            self.glyph_buf[y][x] = glyph_name
            self.text_buf[y][x] = ' '
            return 1
        return 0

    def clear(self):
        for row in range(self.rows):
            for col in range(self.cols):
                self.text_buf[row][col] = ' '
                self.glyph_buf[row][col] = ' '
    def get_dimensions(self):
        return (self.cols, self.rows)

    def fill(self, color):
        """Hardware test: Fill the entire screen with a 16-bit RGB565 color."""
        self._fill_rect(0, 0, 320, 240, color)

    def draw_checkerboard(self, tile_size=8):
        """Hardware test: Draw a full-screen checkerboard pattern."""
        white = COLOR_WHITE
        black = COLOR_BLACK
        for y in range(0, 240, tile_size):
            for x in range(0, 320, tile_size):
                is_white = ((x // tile_size) + (y // tile_size)) % 2 == 0
                color = white if is_white else black
                # Use _fill_rect but constrain height and width at boundaries
                w = min(tile_size, 320 - x)
                h = min(tile_size, 240 - y)
                self._fill_rect(x, y, w, h, color)

