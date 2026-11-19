import logging

logger = logging.getLogger(__name__)

if __debug__: logger.debug("waveshare_esp32_s3_touch_lcd_3_5b.py initialization")
# Hardware initialization for the Waveshare ESP32-S3-Touch-LCD-3.5B:
# 3.5" 320x480 IPS (AXS15231B over QSPI), AXS15231 capacitive touch (I2C),
# QMI8658 IMU, PCF85063 RTC, AXP2101 power management, ES8311 audio codec,
# TF card slot, camera connector (OV5640/OV2640 supported but NOT included
# with the board).
#
# Manufacturer's wiki: https://www.waveshare.com/wiki/ESP32-S3-Touch-LCD-3.5B
#
# Note that this is a different board from the ESP32-S3-Touch-LCD-3.5 (no B)
# supported by waveshare_esp32_s3_touch_lcd_3_5.py: that one drives an ST7796
# over plain SPI with an FT6336 touch controller, this one drives an
# AXS15231B over QSPI with an AXS15231 touch controller.  Only the I2C
# peripherals (PMU, IMU, codec) and the camera wiring are the same.
#
# Pin and reset data taken from Waveshare's own ESP-IDF demo for this board
# (ESP-IDF/01_factory): bsp_display.h, bsp_i2c.h, bsp_touch.h, bsp_es8311.c,
# bsp_camera.c and main/main.cpp:io_expander_init().
#
# The LCD reset line is NOT a GPIO: it is P1 of the on-board TCA9554 I2C IO
# expander (0x20).  Until it is driven high the panel stays in reset, so the
# expander is pulsed before the display driver is created.  There is also no
# D/C line: in QSPI mode every command is sent as a 32 bit word (0x02 for
# parameters, 0x32 for pixel data), so the bus is created with dc=-1 and
# spi_mode=3, matching AXS15231B_PANEL_IO_QSPI_CONFIG() in the Espressif
# component and Waveshare's factory demo.

import time

import drivers.display.axs15231b as axs15231b
import drivers.indev.axs15231 as axs15231
import i2c
import lcd_bus
import lvgl as lv
import machine
import mpos.ui
from drivers.io_expander.pca9554 import PCA9554  # TCA9554, same registers
from mpos import InputManager

# Pin configuration (Waveshare demo / schematic)
SPI_HOST = 1          # SPI2_HOST
SPI_FREQ = 40000000   # 40 MHz, same as Waveshare's EXAMPLE_LCD_PIXEL_CLOCK_HZ
LCD_SCLK = 5
LCD_D0 = 1
LCD_D1 = 2
LCD_D2 = 3
LCD_D3 = 4
LCD_CS = 12
LCD_DC = -1           # no D/C line in QSPI mode (AXS15231B_PANEL_IO_QSPI_CONFIG
                      # in the Espressif component uses dc_gpio_num = -1 too)
LCD_BL = 6

I2C_SDA = 8
I2C_SCL = 7

EXPANDER_ADDR = 0x20   # TCA9554
EXIO_LCD_RESET = 1     # P1 -> LCD_RST (active low, drive high to release)

TOUCH_ADDR = 0x3B      # AXS15231
PMU_ADDR = 0x34        # AXP2101
IMU_ADDR = 0x6B        # QMI8658

# Shared I2C bus: TCA9554 expander, AXS15231 touch, AXP2101 PMU, QMI8658
# IMU, PCF85063 RTC (0x51), ES8311 codec (0x18).
i2c_bus = i2c.I2C.Bus(host=0, scl=I2C_SCL, sda=I2C_SDA, freq=400000, use_locks=False)

# Release the LCD reset through the IO expander BEFORE talking to the
# display controller: P1 high -> low -> high, as main.cpp:io_expander_init()
# and the Arduino examples do.
try:
    expander = PCA9554(i2c_bus, EXPANDER_ADDR)
    expander.set_output(EXIO_LCD_RESET, True)
    time.sleep_ms(10)
    expander.set_output(EXIO_LCD_RESET, False)
    time.sleep_ms(100)
    expander.set_output(EXIO_LCD_RESET, True)
    time.sleep_ms(200)
except Exception as e:
    logger.error("TCA9554 LCD reset failed: %s" % (e))

if __debug__: logger.debug("waveshare_esp32_s3_touch_lcd_3_5b.py machine.SPI.Bus() initialization")
try:
    spi_bus = machine.SPI.Bus(
        host=SPI_HOST,
        sck=LCD_SCLK,
        quad_pins=(LCD_D0, LCD_D1, LCD_D2, LCD_D3)
    )
except Exception as e:
    logger.error("Error initializing SPI bus: %s" % (e))
    if __debug__: logger.debug("Attempting hard reset in 3sec...")
    time.sleep(3)
    machine.reset()

display_bus = lcd_bus.SPIBus(
    spi_bus=spi_bus,
    freq=SPI_FREQ,
    dc=LCD_DC,
    cs=LCD_CS,
    spi_mode=3,  # AXS15231B wants CPOL=1/CPHA=1 (official QSPI config macro)
    quad=True
)

# The AXS15231B over QSPI is a sequential pixel stream, not a windowed
# display (see esp_lcd_axs15231b.c: no RASET, RAMWR only at y=0, RAMWRC
# after).  Arbitrary partial updates are impossible, so the driver uses a
# single full-frame master buffer (320*480*2 = 307200 bytes) in the 8MB
# octal PSRAM and streams the whole frame per refresh - exactly what
# Waveshare's factory demo does (main.cpp: MALLOC_CAP_SPIRAM buffer + one
# full-screen esp_lcd_panel_draw_bitmap per frame).  The buffer MUST be a
# single one: with two alternating buffers in LVGL PARTIAL mode the two
# buffers diverge (each only holds its own dirty areas).
_FRAME_SIZE = 320 * 480 * 2
fb1 = None
fb2 = None
try:
    fb1 = display_bus.allocate_framebuffer(_FRAME_SIZE, lcd_bus.MEMORY_SPIRAM)
    if __debug__: logger.debug("frame buffer: %s bytes (full frame, SPIRAM)" % _FRAME_SIZE)
except Exception as e:
    logger.error("full-frame SPIRAM allocation failed: %s" % (e))

if fb1 is None:
    # Degraded fallback (should never happen on this 8MB PSRAM board):
    # strip buffers in internal DMA RAM.  The driver then runs without
    # accumulate mode; only full-screen sequential refreshes render
    # correctly, because the panel has no usable row window over QSPI.
    logger.error("full-frame SPIRAM buffer unavailable, falling back to strip buffers")
    for _lines in (48, 24, 12):
        _size = 320 * _lines * 2
        try:
            fb1 = display_bus.allocate_framebuffer(
                _size, lcd_bus.MEMORY_INTERNAL | lcd_bus.MEMORY_DMA
            )
            fb2 = display_bus.allocate_framebuffer(
                _size, lcd_bus.MEMORY_INTERNAL | lcd_bus.MEMORY_DMA
            )
            break
        except MemoryError:
            if fb1 is not None:
                display_bus.free_framebuffer(fb1)
            fb1 = None
            fb2 = None

mpos.ui.main_display = axs15231b.AXS15231B(
    data_bus=display_bus,
    frame_buffer1=fb1,
    frame_buffer2=fb2,
    display_width=320,
    display_height=480,
    # color_space / rgb565_byte_swap are chosen by the driver itself:
    # native RGB565 in the frame buffer, byte swap on the wire done by the
    # bus (lcd_panel_io_tx_color).  FULL render mode is mandatory here - see
    # the architecture note at the top of drivers/display/axs15231b.
    color_byte_order=axs15231b.BYTE_ORDER_RGB,
    backlight_pin=LCD_BL,
    backlight_on_state=axs15231b.STATE_PWM,
)  # triggers lv.init()
mpos.ui.main_display.init()
mpos.ui.main_display.set_power(True)
mpos.ui.main_display.set_backlight(100)

# Touch handling:
touch_dev = i2c.I2C.Device(bus=i2c_bus, dev_id=TOUCH_ADDR, reg_bits=8)
indev = axs15231.AXS15231(touch_dev)
InputManager.register_indev(indev)

# The panel is used in its native portrait orientation (320x480).  Hardware
# rotation is not wired up for this controller: display_driver_framework
# sends MADCTL straight to the bus on a rotation change, which bypasses the
# QSPI command encoding this panel needs.

# === POWER MANAGEMENT (AXP2101) ===
# Battery charge/percentage via the PMU instead of a raw ADC pin.
# Same approach as lilygo_t_watch_s3_plus and waveshare_esp32_s3_touch_lcd_3_5.
try:
    from drivers.power.AXP2101 import AXP2101

    from mpos import BatteryManager
    pmu = AXP2101(i2c_bus, addr=PMU_ADDR)
    BatteryManager.read_raw_adc = lambda *args: 0
    BatteryManager.has_battery = lambda *args: True
    BatteryManager.get_battery_percentage = pmu.getBatteryPercent
except Exception as e:
    logger.error("AXP2101 init failed: %s" % (e))

# === SENSOR HARDWARE ===
# QMI8658 IMU on the shared I2C bus.  Mounted position needs on-hardware
# verification; FACING_EARTH matches the boards that use the same IMU.
try:
    from mpos import SensorManager

    SensorManager.init(i2c_bus, address=IMU_ADDR, mounted_position=SensorManager.FACING_EARTH)
except Exception as e:
    logger.error("QMI8658 init failed: %s" % (e))

# === AUDIO (ES8311 codec, onboard speaker connector + microphone) ===
# I2S pins from Waveshare's demo (bsp_es8311.c): MCLK=44, BCLK=13,
# LRCK/WS=15, ESP32->codec (playback)=16, codec->ESP32 (microphone)=14.
# Codec control is I2C @0x18 on the shared bus.
_es8311 = None
try:
    import time as _time

    import drivers.codec.es8311 as es8311_drv

    class _CodecI2C:
        """Adapt the lcd_bus i2c wrapper to the machine.I2C-style API the
        ES8311 driver expects (writeto_mem/readfrom_mem_into)."""

        def __init__(self, bus, dev_id):
            self._dev = i2c.I2C.Device(bus=bus, dev_id=dev_id, reg_bits=8)

        def writeto_mem(self, addr, reg, data):
            self._dev.write_mem(reg, data)

        def readfrom_mem_into(self, addr, reg, buf):
            self._dev.read_mem(reg, buf=buf)

    _es8311 = es8311_drv.ES8311(_CodecI2C(i2c_bus, 0x18))
    _es8311.set_dac_volume(76)
except Exception as e:
    logger.error("ES8311 init failed: %s" % (e))


def _audio_on_open():
    """Called after MCLK starts and before I2S init: release DAC soft-mute."""
    if _es8311:
        _time.sleep_ms(10)
        _es8311.dac_mute(False)


def _audio_on_close():
    """Called before I2S deinit: soft-mute the DAC to suppress pops."""
    if _es8311:
        _es8311.dac_mute(True)
        _time.sleep_ms(20)


if _es8311:
    from mpos import AudioManager

    AudioManager.add(
        AudioManager.Output(
            name="Speaker",
            kind="i2s",
            channels=1,
            i2s_pins={
                'mck': 44,  # MCLK - 256 x sample_rate during playback
                'sck': 13,  # BCLK
                'ws':  15,  # LRCK
                'sd':  16,  # I2S TX (ESP32 -> ES8311 DAC)
            },
            on_open=_audio_on_open,
            on_close=_audio_on_close,
        )
    )

    AudioManager.add(
        AudioManager.Input(
            name="Microphone",
            kind="i2s",
            channels=1,
            i2s_pins={
                'mck':   44,
                'sck':   13,
                'ws':    15,
                'sd_in': 14,  # I2S RX (ES8311 ADC -> ESP32)
            },
            preferred_sample_rate=16000,
        )
    )

# === CAMERA ===
# The board has a camera connector (OV5640/OV2640 supported, none included).
# Pins from Waveshare's demo (bsp_camera.c): identical to the non-B 3.5.
from mpos import CameraManager


def init_cam(width, height, colormode):
    toreturn = None
    try:
        from camera import Camera, GrabMode, PixelFormat

        frame_size = CameraManager.resolution_to_framesize(width, height)
        if __debug__: logger.debug("init_cam: FrameSize %s for %sx%s" % (frame_size, width, height))

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                cam = Camera(
                        data_pins=[45, 47, 48, 46, 42, 40, 39, 21],  # Y2..Y9
                        vsync_pin=17,
                        href_pin=18,
                        # SCCB shares the board's main I2C bus (sda=8/scl=7)
                        # with touch/codec/expander. Reuse the existing bus
                        # driver (port 0) instead of fighting it.
                        sda_pin=-1,
                        scl_pin=-1,
                        sccb_i2c_port=0,
                        pclk_pin=41,
                        xclk_pin=38,
                        xclk_freq=20000000,
                        powerdown_pin=-1,
                        reset_pin=-1,
                        pixel_format=PixelFormat.RGB565 if colormode else PixelFormat.GRAYSCALE,
                        frame_size=frame_size,
                        grab_mode=GrabMode.LATEST,
                        fb_count=1
                    )
                toreturn = cam
                break
            except Exception as e:
                if attempt < max_attempts - 1:
                    logger.error("init_cam attempt %s failed: %s, retrying..." % (attempt, e))
                else:
                    logger.error("init_cam final exception: %s" % (e))
                    break
    except Exception as e:
        logger.error("init_cam exception: %s" % (e))
    return toreturn


def deinit_cam(cam):
    cam.deinit()


def capture_cam(cam_obj, colormode):
    return cam_obj.capture()


def apply_cam_settings(cam_obj, prefs):
    return CameraManager.ov_apply_camera_settings(cam_obj, prefs)


CameraManager.add_camera(CameraManager.Camera(
    lens_facing=CameraManager.CameraCharacteristics.LENS_FACING_BACK,
    name="Camera connector (OV5640/OV2640)",
    vendor="OmniVision",
    init=init_cam,
    deinit=deinit_cam,
    capture=capture_cam,
    apply_settings=apply_cam_settings,
    rotation_degrees=90,
))

if __debug__: logger.debug("waveshare_esp32_s3_touch_lcd_3_5b.py finished")
