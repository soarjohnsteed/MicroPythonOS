# Based on the work by straga (https://github.com/straga)
# https://github.com/straga/micropython_lcd/blob/master/device/JC3248W535/driver/axs15231b/axs15231_touch.py
# Copyright (c) 2024 - 2025 Kevin G. Schlosser
#
# Imported into MicroPythonOS for the Waveshare ESP32-S3-Touch-LCD-3.5B
# (AXS15231 capacitive controller on I2C address 0x3B, sharing the bus with
# the TCA9554 IO expander, the AXP2101 PMU, the QMI8658 IMU, the PCF85063
# RTC and the ES8311 codec).
#
# MicroPythonOS changes:
#   * lv is imported explicitly.  The upstream default argument read
#     lv.DISPLAY_ROTATION._0 with no `import lvgl` anywhere in the file, which
#     raises NameError the moment the module is imported (default arguments are
#     evaluated at class-creation time, not at call time).
#     Note: reading it as pointer_framework.lv.DISPLAY_ROTATION._0 also works
#     -- cst816s.py, ft6x36.py and gt911.py in this tree all do that, because
#     frozen pointer_framework.py happens to do `import lvgl as lv` at module
#     level.  But that is an implicit dependency on another module's import
#     side effect: it silently breaks if pointer_framework ever stops
#     importing lvgl.  xpt2046.py shows the explicit form works just as well,
#     so prefer it -- lvgl is a frozen module and is always available here.
#   * coordinates outside the panel are dropped.  The controller reports
#     garbage frames while idle (see Waveshare's own esp_lcd_touch_axs15231b
#     demo, which filters them), and those turned into phantom presses.

import lvgl as lv  # NOQA
from micropython import const  # NOQA
import pointer_framework


# Constants
_AXS_MAX_TOUCH_NUMBER = const(1)
_AXS_TOUCH_POINT_NUM = const(1)
I2C_ADDR = 0x3B
BITS = 8


class TouchRecord:
    def __init__(self):
        self.gesture = 0
        self.num = 0
        self.x = 0
        self.y = 0
        self.event = 0


class AXS15231(pointer_framework.PointerDriver):

    def __init__(
        self,
        device,
        touch_cal=None,
        startup_rotation=lv.DISPLAY_ROTATION._0,  # NOQA
        debug=False
    ):
        self._device = device

        super().__init__(touch_cal, startup_rotation, debug)

        # panel size, used to throw away out of range (garbage) reports
        self._max_x = self._orig_width
        self._max_y = self._orig_height

        self._tx_buf = bytearray(
            [0xB5, 0xAB, 0xA5, 0x5A, 0x00, 0x00, 0x00, 0x08, 0x00, 0x00, 0x00]
        )
        self._tx_mv = memoryview(self._tx_buf)

        self._rx_buf = bytearray(8)
        self._rx_mv = memoryview(self._rx_buf)

        self.__last_x = -1
        self.__last_y = -1
        self.__last_state = self.RELEASED

    def _read_data(self):
        self._device.write(self._tx_mv)
        self._device.read(buf=self._rx_mv)

        touch_points = []
        data = self._rx_buf

        num_points = data[_AXS_TOUCH_POINT_NUM]

        if num_points and num_points <= _AXS_MAX_TOUCH_NUMBER:
            for i in range(num_points):
                offset = i * 6
                gesture = data[offset]
                num = data[offset + 1]
                x = ((data[offset + 2] & 0x0F) << 8) | data[offset + 3]
                y = ((data[offset + 4] & 0x0F) << 8) | data[offset + 5]

                # Drop the idle/garbage frames the controller keeps sending
                # when nobody is touching the screen.
                if not num or x >= self._max_x or y >= self._max_y:
                    continue

                record = TouchRecord()
                record.gesture = gesture
                record.num = num
                record.x = x
                record.y = y
                record.event = (data[offset + 2] >> 6) & 0x03
                touch_points.append(record)

        return touch_points

    def _get_coords(self):
        touch_data = self._read_data()

        if touch_data:
            self.__last_x = touch_data[0].x
            self.__last_y = touch_data[0].y

            if touch_data[0].event == 1:
                self.__last_state = self.RELEASED

            else:
                self.__last_state = self.PRESSED

        return self.__last_state, self.__last_x, self.__last_y
