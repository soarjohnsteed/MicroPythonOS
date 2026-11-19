# AXS15231B display driver for MicroPythonOS
#
# Derived from the lvgl_micropython driver
# (api_drivers/common_api_drivers/display/axs15231b/axs15231b.py), which is
# itself based on the work by straga:
# https://github.com/straga/micropython_lcd/tree/master/device/JC3248W535
#
# Target: Waveshare ESP32-S3-Touch-LCD-3.5B (320x480, QSPI, AXS15231B)
#
# ---------------------------------------------------------------------------
# 1. THE PANEL IS A SEQUENTIAL PIXEL STREAM (QSPI)
# ---------------------------------------------------------------------------
# Verified against Espressif's esp_lcd_axs15231b.c (panel_axs15231b_draw_bitmap)
# and esp_lcd/spi/esp_lcd_panel_io_spi.c:
#
#   * CASET (0x2A) sets the column window.  RASET (0x2B) is NOT sent in QSPI
#     mode - the panel does not honour a row window, the row position is
#     implicit in the stream.
#   * RAMWR (0x2C) is only legal as the FIRST write of a frame (y_start == 0).
#     It resets the GRAM pointer to the top of the column window.
#   * Every later chunk of the same frame must use RAMWRC (0x3C, write memory
#     continue).
#   * QSPI packs everything into one 32 bit command word:
#         0x02 = write register      0x32 = write colour
#         word = (opcode << 24) | (register << 8)
#
# 2. THE FRAME BUFFER MUST BE DRIVEN IN FULL RENDER MODE
# ---------------------------------------------------------------------------
# This driver streams the WHOLE frame on every refresh, so the buffer has to
# always contain a complete image.  That is only true in
# lv.DISPLAY_RENDER_MODE.FULL.  Verified against lv_refr.c:
#
#   * FULL:    "If there were at least 1 invalid area in full refresh mode,
#               redraw the whole screen" -> disp->inv_areas[0] = scr_area.
#              refr_area() then sets layer->buf_area to the whole screen, so
#              the dirty rect is drawn at its REAL position in the buffer.
#   * PARTIAL: refr_area() sets layer->buf_area = the dirty sub area and
#              reshapes the draw buffer to that sub area, i.e. the pixels
#              land at offset 0 of the buffer, NOT at their screen position.
#
# Using PARTIAL on a full size buffer therefore renders only the dirty rect
# into the top-left corner and keeps whatever the FIRST frame left in the
# rest of the buffer - which is an empty (black) screen.  That is exactly the
# black screen this driver used to show.  Do NOT switch it back to PARTIAL.
#
# 3. COLOUR PATH
# ---------------------------------------------------------------------------
# lvgl renders native (little endian) RGB565 into the buffer and the bus does
# the byte swap on the wire, in place, once per chunk
# (lcd_types.c: lcd_panel_io_tx_color -> rgb565_byte_swap()).  Because FULL
# mode rewrites every pixel on every refresh, the in place swap is idempotent
# across frames: each byte pair is swapped exactly once before it goes out and
# LVGL refills the whole buffer afterwards.
#
# lv.COLOR_FORMAT.RGB565_SWAPPED is deliberately NOT used: it is only safe
# when the firmware was built with LV_DRAW_SW_SUPPORT_RGB565_SWAPPED=1, and
# it is not worth the risk for a pure optimisation.
#
# 4. FLUSH HANDLING
# ---------------------------------------------------------------------------
# esp_lcd_panel_io_spi.c fires on_color_trans_done exactly once per
# tx_color() call (en_trans_done_cb is only set on the last internal chunk),
# and it also blocks until the previously queued chunks have finished before
# it sends the next command.  The ISR callback therefore only counts the
# chunks down; flush_ready() is raised from normal (non ISR) context at the
# end of _flush_cb after a bounded wait, so a lost callback degrades to
# tearing instead of hanging LVGL inside while (disp->flushing);
# ---------------------------------------------------------------------------

import display_driver_framework
from micropython import const  # NOQA

import time

import lcd_bus
import lvgl as lv  # NOQA


STATE_HIGH = display_driver_framework.STATE_HIGH
STATE_LOW = display_driver_framework.STATE_LOW
STATE_PWM = display_driver_framework.STATE_PWM

BYTE_ORDER_RGB = display_driver_framework.BYTE_ORDER_RGB
BYTE_ORDER_BGR = display_driver_framework.BYTE_ORDER_BGR

_CASET = const(0x2A)
_RASET = const(0x2B)
_MADCTL = const(0x36)

_RAMWR = const(0x2C)
_RAMWRC = const(0x3C)

_MADCTL_MH = const(0x04)  # Refresh 0=Left to Right, 1=Right to Left
_MADCTL_BGR = const(0x08)  # BGR color order
_MADCTL_ML = const(0x10)  # Refresh 0=Top to Bottom, 1=Bottom to Top

_MADCTL_MV = const(0x20)  # 0=Normal, 1=Row/column exchange
_MADCTL_MX = const(0x40)  # 0=Left to Right, 1=Right to Left
_MADCTL_MY = const(0x80)  # 0=Top to Bottom, 1=Bottom to Top

# QSPI command markers, see the note above
_WRITE_CMD = const(0x02)
_WRITE_COLOR = const(0x32)

_WRDISBV = const(0x51)
_SW_RESET = const(0x01)

_PARAM_BUF_SIZE = const(64)

# Panel stream chunk: 48 rows of 320 px * 2 bytes.  307200 / 30720 = 10
# chunks exactly.  Small enough to stay well below the per transaction SPI
# limit, large enough to keep the command overhead negligible.
_STREAM_CHUNK = const(30720)

# Upper bound for "all chunks left the chip".  Normally this takes ~15 ms;
# hitting it means the trans-done callback is missing and we let LVGL carry
# on instead of deadlocking it.
_FLUSH_TIMEOUT_MS = const(250)


class AXS15231B(display_driver_framework.DisplayDriver):

    _ORIENTATION_TABLE = (
        0,
        _MADCTL_MV,
        _MADCTL_MX | _MADCTL_MY,
        _MADCTL_MV | _MADCTL_MX | _MADCTL_MY
    )

    def __init__(
        self,
        data_bus,
        display_width,
        display_height,
        frame_buffer1=None,
        frame_buffer2=None,
        reset_pin=None,
        reset_state=STATE_HIGH,
        power_pin=None,
        power_on_state=STATE_HIGH,
        backlight_pin=None,
        backlight_on_state=STATE_HIGH,
        offset_x=0,
        offset_y=0,
        color_byte_order=BYTE_ORDER_RGB,
        color_space=None,
        rgb565_byte_swap=None
    ):
        if not isinstance(
            data_bus,
            (lcd_bus.RGBBus, lcd_bus.I80Bus, lcd_bus.SPIBus)
        ):
            raise RuntimeError('incompatible bus driver')

        num_lanes = data_bus.get_lane_count()
        self._qspi = (
            isinstance(data_bus, lcd_bus.SPIBus) and num_lanes == 4
        )

        if self._qspi:
            self._caset = (_WRITE_CMD << 24) | (_CASET << 8)
            self._raset = (_WRITE_CMD << 24) | (_RASET << 8)
            self._madctl_cmd = (_WRITE_CMD << 24) | (_MADCTL << 8)
            self._ramwr = (_WRITE_COLOR << 24) | (_RAMWR << 8)
            self._ramwrc = (_WRITE_COLOR << 24) | (_RAMWRC << 8)
            _cmd_bits = 32
        else:
            self._caset = _CASET
            self._raset = _RASET
            self._madctl_cmd = _MADCTL
            self._ramwr = _RAMWR
            self._ramwrc = _RAMWRC
            _cmd_bits = 8

        if color_space is None:
            color_space = lv.COLOR_FORMAT.RGB565

        if rgb565_byte_swap is None:
            rgb565_byte_swap = True

        self._brightness = 0xD0
        self._pending_chunks = 0

        # Diagnostics for the "flickers while swiping" report.  Cheap enough
        # to leave in permanently (two ticks_ms() per frame) and they are the
        # only way to tell "transfer is slow" apart from "trans-done callback
        # counting is wrong".  See flush_stats().
        self._flush_frames = 0
        self._flush_ms_total = 0
        self._flush_timeouts = 0
        self._cbs_seen = 0
        self._cbs_expected = 0

        super().__init__(
            data_bus,
            display_width,
            display_height,
            frame_buffer1,
            frame_buffer2,
            reset_pin,
            reset_state,
            power_pin,
            power_on_state,
            backlight_pin,
            backlight_on_state,
            offset_x,
            offset_y,
            color_byte_order,
            color_space,  # NOQA
            rgb565_byte_swap,
            _cmd_bits=_cmd_bits,
            _param_bits=8,
            _init_bus=True
        )

        # See the note at the top of this file: size the shared parameter
        # buffer before anything writes into it (vendor init writes up to
        # 31 bytes at a time).
        if len(self._param_buf) < _PARAM_BUF_SIZE:
            self._param_buf = bytearray(_PARAM_BUF_SIZE)
            self._param_mv = memoryview(self._param_buf)

        # Column and row window slices, reused so the flush path does not
        # allocate.
        self._caset_mv = self._param_mv[:4]
        self._raset_mv = self._param_mv[4:8]

        # Dedicated CASET window for the frame stream.  Must NOT live in
        # _param_buf: the vendor init sequence clobbers that buffer.
        self._win_buf = bytearray((
            (offset_x >> 8) & 0xFF, offset_x & 0xFF,
            ((offset_x + display_width - 1) >> 8) & 0xFF,
            (offset_x + display_width - 1) & 0xFF
        ))

        # True when the buffer covers the whole screen, in which case the
        # framework already selected RENDER_MODE.FULL for us and LVGL always
        # hands us a complete frame.  Anything smaller means strip rendering
        # (degraded fallback) where each flush carries its own y window.
        self._full_frame = (
            len(self._frame_buffer1) ==
            display_width * display_height *
            lv.color_format_get_size(self._color_space)
        )

        print(
            'AXS15231B: %ux%u qspi=%s mode=%s cf=%u swap=%s chunks=%u' % (
                display_width,
                display_height,
                self._qspi,
                'FULL' if self._full_frame else 'PARTIAL(strap fallback)',
                self._color_space,
                self._rgb565_byte_swap,
                (
                    display_width * display_height *
                    lv.color_format_get_size(self._color_space) +
                    _STREAM_CHUNK - 1
                ) // _STREAM_CHUNK
            )
        )

    def reset(self):
        # On the ESP32-S3-Touch-LCD-3.5B the reset line is not a GPIO but a
        # pin of the on-board TCA9554 (I2C 0x20, P1); the board file pulses
        # it before creating this driver.  Fall back to a software reset so
        # the driver stays usable without any reset pin at all.
        if self._reset_pin is None:
            self.set_params(_SW_RESET)
        else:
            self._reset_pin.value(not self._reset_state)
            time.sleep_ms(10)
            self._reset_pin.value(self._reset_state)
            time.sleep_ms(10)
            self._reset_pin.value(not self._reset_state)

        time.sleep_ms(120)

    def set_brightness(self, value):
        value = int(value / 100.0 * 255)
        value = max(0x00, min(value, 0xFF))

        self._brightness = value

        self._param_buf[0] = value
        self.set_params(_WRDISBV, self._param_mv[:1])

    def get_brightness(self):
        return round(self._brightness / 255.0 * 100.0, 1)

    def set_params(self, cmd, params=None):
        if self._qspi:
            cmd = ((cmd & 0xFF) << 8) | (_WRITE_CMD << 24)

        self._data_bus.tx_param(cmd, params)

    def _set_memory_location(self, x1, y1, x2, y2):
        # Only used for the one-time window setup the framework performs in
        # init() and by the degraded strip fallback.  Mirrors
        # esp_lcd_axs15231b.c: QSPI -> CASET only, RAMWR when y1 == 0 else
        # RAMWRC; other buses -> CASET + RASET + RAMWR.
        param_buf = self._param_buf  # NOQA

        param_buf[0] = (x1 >> 8) & 0xFF
        param_buf[1] = x1 & 0xFF
        param_buf[2] = (x2 >> 8) & 0xFF
        param_buf[3] = x2 & 0xFF

        self._data_bus.tx_param(self._caset, self._caset_mv)

        if self._qspi:
            if y1 == 0:
                return self._ramwr
            return self._ramwrc

        param_buf[4] = (y1 >> 8) & 0xFF
        param_buf[5] = y1 & 0xFF
        param_buf[6] = (y2 >> 8) & 0xFF
        param_buf[7] = y2 & 0xFF

        self._data_bus.tx_param(self._raset, self._raset_mv)

        return self._ramwr

    def _dummy_set_memory_location(self, *_, **__):  # NOQA
        # Installed by the framework when the buffer covers the whole
        # screen.  The inherited version returns a bare 0x2C which the
        # panel ignores in QSPI mode.
        return self._ramwr

    # ------------------------------------------------------------------
    # Flush machinery (see the architecture note at the top of this file)
    # ------------------------------------------------------------------

    def _flush_cb(self, disp_drv, area, color_p):
        x1 = area.x1 + self._offset_x
        x2 = area.x2 + self._offset_x
        y1 = area.y1 + self._offset_y
        y2 = area.y2 + self._offset_y

        size = (
            (x2 - x1 + 1) *
            (y2 - y1 + 1) *
            lv.color_format_get_size(self._color_space)
        )

        # we have to use the __dereference__ method because this method is
        # what converts from the C_Array object the binding passes into a
        # memoryview object that can be passed to the bus drivers
        data_view = color_p.__dereference__(size)

        start = time.ticks_ms()
        self._stream(data_view, y1)
        self._wait_stream_done()
        self._flush_frames += 1
        self._flush_ms_total += time.ticks_diff(time.ticks_ms(), start)

        # flush_ready from normal context: LVGL is free to render the next
        # frame into the buffer only after every chunk has left the chip.
        disp_drv.flush_ready()

    def flush_stats(self):
        """Return (frames, avg_flush_ms, timeouts, callbacks, expected).

        Interpretation of a swipe-flicker report:
          * callbacks/frames == 10 (one per 30720 byte chunk) -> the transfer
            completes on its own, so what you see is plain TEARING: the panel
            scans its GRAM while we stream into it, with no vsync/TE sync.
            Only a higher SPI clock or a TE-synchronised flush can fix that.
          * timeouts > 0 -> the trans-done callback is NOT arriving once per
            tx_color(), the wait burns the full 250 ms and the frame rate
            collapses to ~4 fps.  That is a counting bug and it IS fixable.
        """
        frames = self._flush_frames
        avg = (self._flush_ms_total // frames) if frames else 0
        return (frames, avg, self._flush_timeouts, self._cbs_seen, self._cbs_expected)

    def _stream(self, data, y1):
        # CASET once per frame, like panel_axs15231b_draw_bitmap().
        self._data_bus.tx_param(self._caset, self._win_buf)

        n = (len(data) + _STREAM_CHUNK - 1) // _STREAM_CHUNK
        self._pending_chunks = n
        self._cbs_expected += n

        # The panel resets its GRAM pointer on RAMWR, so only the very first
        # chunk of a frame that starts at row 0 may use it.
        cmd = self._ramwr if y1 == 0 else self._ramwrc

        for off in range(0, len(data), _STREAM_CHUNK):
            # x/y/rotation/last_update are ignored by the esp32 lcd_bus
            # glue; the command word carries all the meaning.
            self._data_bus.tx_color(
                cmd, data[off:off + _STREAM_CHUNK], 0, 0, 0, 0, 0, True
            )
            cmd = self._ramwrc

    def _wait_stream_done(self):
        if self._pending_chunks <= 0:
            return

        start = time.ticks_ms()
        while self._pending_chunks > 0:
            if time.ticks_diff(time.ticks_ms(), start) > _FLUSH_TIMEOUT_MS:
                self._flush_timeouts += 1
                self._pending_chunks = 0
                if self._flush_timeouts <= 3:
                    print(
                        'AXS15231B: WARNING trans-done callback missing, '
                        'flush forced (got %u of %u, total %u)'
                        % (self._cbs_seen, self._cbs_expected, self._flush_timeouts)
                    )
                break

    def _flush_ready_cb(self, *_):
        # Runs in ISR context (lcd_types.c: cb_isr) with the GC locked, so it
        # must not allocate.  It only counts the queued chunks down; LVGL is
        # released from _flush_cb in normal context.
        if self._pending_chunks > 0:
            self._pending_chunks -= 1
        self._cbs_seen += 1
