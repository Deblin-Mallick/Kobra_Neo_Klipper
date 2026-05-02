# Support for ST7789V 320x240 color TFT displays (SPI)
# Used on Anycubic Kobra Neo and similar printers
#
# Copyright (C) 2024
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
from .. import bus
from . import font8x14

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
    """320x240 color TFT via SPI with DC pin. Renders Klipper's standard
    16x4 character display as large colored text on the TFT."""
    def __init__(self, config, io):
        self.send = io.send
        # Display is 320x240, we render a 16x4 character grid
        # Each character is 20x60 pixels (font8x14 scaled 2.5x horizontally, 4x vertically)
        # We use 2x horizontal scale = 16px wide, 2x vertical = 28px tall
        # That gives 320/16=20 chars wide, 240/28=8 rows — use 20x8 grid
        self.cols = 20
        self.rows = 8
        self.char_w = 16  # pixels per char horizontally (8*2)
        self.char_h = 28  # pixels per char vertically (14*2)
        # Character buffer (what's currently displayed)
        self.text_buf = [[' '] * self.cols for _ in range(self.rows)]
        self.old_text_buf = [['~'] * self.cols for _ in range(self.rows)]
        self.icons = {}
        self.font = font8x14.VGA_FONT
        self._init_display()

    def _init_display(self):
        """Send ST7789V initialization sequence."""
        # Sleep out
        self.send([0x11])
        import time; time.sleep(0.12)
        # MADCTL: landscape MY|MV|ML = 0xB0
        self.send([0x36]); self.send([0xB0], is_data=True)
        # Pixel format 16-bit
        self.send([0x3A]); self.send([0x05], is_data=True)
        # Frame rate
        self.send([0xB2]); self.send([0x05, 0x05, 0x00, 0x33, 0x33], is_data=True)
        self.send([0xB7]); self.send([0x35], is_data=True)
        # Power
        self.send([0xBB]); self.send([0x28], is_data=True)
        self.send([0xC0]); self.send([0x2C], is_data=True)
        self.send([0xC2]); self.send([0x01], is_data=True)
        self.send([0xC3]); self.send([0x0B], is_data=True)
        self.send([0xC4]); self.send([0x20], is_data=True)
        self.send([0xC6]); self.send([0x0F], is_data=True)
        self.send([0xD0]); self.send([0xA4, 0xA1], is_data=True)
        # Gamma
        self.send([0xE0]); self.send([0xD0,0x01,0x08,0x0F,0x11,0x2A,0x36,
                                      0x55,0x44,0x3A,0x0B,0x06,0x11,0x20], is_data=True)
        self.send([0xE1]); self.send([0xD0,0x02,0x07,0x0A,0x0B,0x18,0x34,
                                      0x43,0x4A,0x2B,0x1B,0x1C,0x22,0x1F], is_data=True)
        # Display ON
        self.send([0x29])
        import time; time.sleep(0.05)
        # Clear to black
        self._fill_rect(0, 0, 320, 240, COLOR_BLACK)

    def _set_window(self, x, y, w, h):
        """Set the address window for pixel writes."""
        xe = x + w - 1
        ye = y + h - 1
        self.send([0x2A])  # Column address
        self.send([x >> 8, x & 0xFF, xe >> 8, xe & 0xFF], is_data=True)
        self.send([0x2B])  # Row address
        self.send([y >> 8, y & 0xFF, ye >> 8, ye & 0xFF], is_data=True)
        self.send([0x2C])  # Memory write

    def _fill_rect(self, x, y, w, h, color):
        """Fill a rectangle with a solid color."""
        self._set_window(x, y, w, h)
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF
        # Send in chunks to avoid huge buffers
        row_data = bytearray([hi, lo] * w)
        for _ in range(h):
            self.send(row_data, is_data=True)

    def _draw_char(self, col, row, ch, fg=COLOR_WHITE, bg=COLOR_BLACK):
        """Draw a single character at grid position (col, row), scaled 2x."""
        x = col * self.char_w
        y = row * self.char_h
        if x >= 320 or y >= 240:
            return
        c = ord(ch) if isinstance(ch, str) else ch
        if c > 127:
            c = ord('?')
        glyph = self.font[c]  # 14 bytes, each byte is a row of 8 pixels
        # Build scaled pixel data: 16px wide x 28px tall (2x scale)
        pixels = bytearray(self.char_w * self.char_h * 2)
        idx = 0
        fg_hi, fg_lo = (fg >> 8) & 0xFF, fg & 0xFF
        bg_hi, bg_lo = (bg >> 8) & 0xFF, bg & 0xFF
        for font_row in range(14):
            row_byte = glyph[font_row]
            # Build one scaled row (16 pixels = 32 bytes)
            scaled_row = bytearray(32)
            for bit in range(8):
                if row_byte & (0x80 >> bit):
                    scaled_row[bit*4] = fg_hi
                    scaled_row[bit*4+1] = fg_lo
                    scaled_row[bit*4+2] = fg_hi
                    scaled_row[bit*4+3] = fg_lo
                else:
                    scaled_row[bit*4] = bg_hi
                    scaled_row[bit*4+1] = bg_lo
                    scaled_row[bit*4+2] = bg_hi
                    scaled_row[bit*4+3] = bg_lo
            # Write same row twice (2x vertical scale)
            pixels[idx:idx+32] = scaled_row
            idx += 32
            pixels[idx:idx+32] = scaled_row
            idx += 32
        self._set_window(x, y, self.char_w, self.char_h)
        self.send(pixels, is_data=True)

    def flush(self):
        """Send changed characters to the display."""
        for row in range(self.rows):
            for col in range(self.cols):
                if self.text_buf[row][col] != self.old_text_buf[row][col]:
                    ch = self.text_buf[row][col]
                    # Color coding: row 0 = cyan header, others white
                    fg = COLOR_CYAN if row == 0 else COLOR_WHITE
                    self._draw_char(col, row, ch, fg=fg, bg=COLOR_BLACK)
                    self.old_text_buf[row][col] = self.text_buf[row][col]

    def set_glyphs(self, glyphs):
        for glyph_name, glyph_data in glyphs.items():
            icon = glyph_data.get('icon16x16')
            if icon is not None:
                self.icons[glyph_name] = icon

    def write_text(self, x, y, data):
        """Write text at character position (x, y)."""
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
        """Write a graphics icon (render as text placeholder for now)."""
        pass

    def write_glyph(self, x, y, glyph_name):
        """Write a named glyph/icon."""
        # For now, use text fallback
        if glyph_name == 'right_arrow':
            self.write_text(x, y, '>')
            return 1
        if glyph_name == 'degrees':
            self.write_text(x, y, '\xf8')
            return 1
        return 0

    def clear(self):
        for row in range(self.rows):
            for col in range(self.cols):
                self.text_buf[row][col] = ' '

    def get_dimensions(self):
        return (self.cols, self.rows)


def lookup_display(config, io):
    return ST7789V(config, io)
