# MicroPythonOS · Waveshare ESP32-S3-Touch-LCD-3.5B

[English](#english) · [中文](#中文)

---

## English

### About

Unofficial board adaptation of [MicroPythonOS](https://github.com/MicroPythonOS/MicroPythonOS) for **Waveshare ESP32-S3-Touch-LCD-3.5B**.

Display and touchscreen work, and the system boots successfully to the desktop UI.

The **AXS15231B + QSPI** display controller requires modifications to several shared files, not just the board definition. Therefore, this adaptation is provided separately rather than submitted as a PR.

The adaptation consists of 7 files, all included in `Waveshare-ESP32-S3-Touch-LCD-3.5B/`.

### Usage

**Option 1: Flash the prebuilt firmware**

The prebuilt firmware is available in the repository root:

`lvgl_micropy_ESP32_GENERIC_S3-SPIRAM_OCT-16.bin`

See [Flashing](#flashing) below.

**Option 2: Build from source**

Clone the repository and copy the adaptation files into the project root, overwriting the corresponding files:

```bash
git clone --recursive https://github.com/soarjohnsteed/MicroPythonOS.git
cd MicroPythonOS

cp -a Waveshare-ESP32-S3-Touch-LCD-3.5B/. .
```

If using the official upstream repository instead, download the adaptation folder separately and copy its contents into the MicroPythonOS source root in the same way.

### Build Requirements

- Linux or WSL2.
- At least 10 GB of free disk space.
- Python virtual environment.
- `littlefs-python==0.19.0` (required for filesystem preloading, but missing from the project's dependency lists).

Install the Python dependency:

```bash
python3 -m venv ~/.venv-mpos-preload
~/.venv-mpos-preload/bin/pip install "littlefs-python==0.19.0"
```

The build script must use this environment for the preload step. Check `MPOS_PRELOAD_PYTHON` in `scripts/build_mpos.sh` and configure it accordingly.

System dependencies:

**Fedora / Nobara:**

```bash
sudo dnf install git wget flex bison gperf python3 python3-devel \
cmake ninja-build ccache libffi-devel openssl-devel dfu-util \
libusbx-devel gcc gcc-c++ make which rsync
```

**Debian / Ubuntu:**

```bash
sudo apt install git wget flex bison gperf python3 python3-venv \
python3-dev cmake ninja-build ccache libffi-dev libssl-dev \
dfu-util libusb-1.0-0-dev build-essential rsync
```

### Build

```bash
# Verify all nested submodules are initialized
git submodule status --recursive | grep -c '^-'
# Must output 0. Otherwise:
git submodule update --init --recursive

# Use the project's pinned ESP-IDF version (5.5.1)
unset IDF_PATH
unset IDF_PYTHON_ENV_PATH

./scripts/build_mpos.sh esp32s3
```

The first build downloads the Xtensa toolchain and typically takes **20–40 minutes**.

For subsequent builds after Python-only changes:

```bash
MPOS_NO_CLEAN=1 ./scripts/build_mpos.sh esp32s3
```

**Check the last three lines of the build output.** They should include:

```text
=== Firmware size check (esp32s3) ===
Packed 1012.8 KiB into preload.img (vfs at 0x900000, 7.0 MiB)
Merged image: .../lvgl_micropy_ESP32_GENERIC_S3-SPIRAM_OCT-16.bin (16.0 MiB)
```

`internal_filesystem/` is embedded into the firmware during compilation. Changes to its Python files require rebuilding; copying files to the device will not update them.

### Flashing

Flash the **entire 16 MB image** at address `0x0`. It already contains the bootloader, partition table, otadata, application and preloaded filesystem.

No `erase_flash` is required.

```bash
esptool.py --chip esp32s3 -p /dev/ttyACM0 -b 921600 \
  --before default_reset --after hard_reset \
  write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m \
  0x0 lvgl_micropy_ESP32_GENERIC_S3-SPIRAM_OCT-16.bin
```

- Image size must be exactly `16777216` bytes.
- Serial permissions: add your user to `dialout` and log in again, or run `newgrp dialout`.

  ```bash
  sudo usermod -aG dialout $USER
  ```

- Fedora / Nobara: prevent serial port conflicts:

  ```bash
  sudo systemctl mask --now brltty
  sudo systemctl stop ModemManager
  ```

Serial console:

```bash
tio /dev/ttyACM0 -b 115200
# Or: screen /dev/ttyACM0 115200
```

### Verification

Expected display initialization output:

```text
AXS15231B: 320x480 qspi=True mode=FULL cf=7 swap=True chunks=10
```

- `qspi=True`: board detected as the 3.5B variant (I2C probes at `0x20` and `0x3B`).
- `mode=FULL`: full-frame refresh is active. `PARTIAL(strap fallback)` indicates fallback mode.
- Display remains on without going black; touchscreen responds correctly.

### Troubleshooting

| Symptom | Cause / Solution |
|---|---|
| `Missing esp-mqtt submodule` | Nested submodules were not fully initialized. Run `git submodule update --init --recursive` inside `lvgl_micropython/lib/esp-idf`. |
| `littlefs-python is required to build the preloaded filesystem` | Install `littlefs-python==0.19.0`. The firmware build has not actually completed. |
| `Permission denied` on `/dev/ttyACM0` | User is not in `dialout`. Run `newgrp dialout` or log in again. |
| Serial port busy or device disappears | `brltty` or `ModemManager` may be occupying the port. Stop or mask them. |
| Device keeps rebooting after flashing | Wrong image (e.g. the 3.4 MB application-only image), or an incompatible partition table remains in flash. Flash the complete 16 MB image. |
| Occasional flickering when returning to certain MicroPythonOS pages | Known minor issue; cause unknown. |

### Notes

- Only **68 KB (2%)** of the application partition remains free. Additional features may exceed the partition limit and require component removal or partition resizing.
- When switching between this firmware and ESP-IDF projects on the same board, flash the complete image at `0x0`, or use `idf.py flash --erase-all`. Their partition layouts are different.

### Credits

- [MicroPythonOS](https://github.com/MicroPythonOS/MicroPythonOS) — Operating system.
- [lvgl_micropython](https://github.com/lvgl/lvgl_micropython) — Build system.
- Authors: [straga](https://github.com/straga) and [de-dh](https://github.com/de-dh).
- References:
  - [micropython_lcd – JC3248W535](https://github.com/straga/micropython_lcd/tree/master/device/JC3248W535) — QSPI initialization sequence reference.
  - [ESP32-JC3248W535-Micropython-LVGL](https://github.com/de-dh/ESP32-JC3248W535-Micropython-LVGL) — QSPI initialization sequence reference.

---

## 中文

### 这是什么

给 **Waveshare ESP32-S3-Touch-LCD-3.5B** 做的 [MicroPythonOS](https://github.com/MicroPythonOS/MicroPythonOS) 板级移植。

显示和触摸均可用，系统能正常启动到桌面 UI。

由于 **AXS15231B + QSPI** 驱动较特殊，无法仅修改板定义来适配，必须修改一些公用文件，因此没有提交 PR。

适配共涉及 7 个文件，全部放在 `Waveshare-ESP32-S3-Touch-LCD-3.5B/` 目录中。

### 用法

**方式一：直接烧录预编译固件**

预编译固件位于仓库根目录：

`lvgl_micropy_ESP32_GENERIC_S3-SPIRAM_OCT-16.bin`

烧录方法见下文。

**方式二：自行编译**

克隆仓库，并将适配目录中的内容覆盖到项目根目录：

```bash
git clone --recursive https://github.com/soarjohnsteed/MicroPythonOS.git
cd MicroPythonOS

cp -a Waveshare-ESP32-S3-Touch-LCD-3.5B/. .
```

如果使用官方上游仓库，则单独下载适配目录，再以相同方式将其中的内容复制到 MicroPythonOS 源码根目录。

### 编译环境

- Linux / WSL2。
- 至少 10 GB 可用磁盘空间。
- Python 虚拟环境。
- `littlefs-python==0.19.0`（预加载文件系统所需，但项目依赖清单中遗漏了它）。

安装 Python 依赖：

```bash
python3 -m venv ~/.venv-mpos-preload
~/.venv-mpos-preload/bin/pip install "littlefs-python==0.19.0"
```

构建脚本的预加载环节需要使用这个 Python 环境。检查 `scripts/build_mpos.sh` 中的 `MPOS_PRELOAD_PYTHON` 并按需配置。

系统依赖：

**Fedora / Nobara：**

```bash
sudo dnf install git wget flex bison gperf python3 python3-devel \
cmake ninja-build ccache libffi-devel openssl-devel dfu-util \
libusbx-devel gcc gcc-c++ make which rsync
```

**Debian / Ubuntu：**

```bash
sudo apt install git wget flex bison gperf python3 python3-venv \
python3-dev cmake ninja-build ccache libffi-dev libssl-dev \
dfu-util libusb-1.0-0-dev build-essential rsync
```

### 编译

```bash
# 检查所有嵌套子模块
git submodule status --recursive | grep -c '^-'
# 必须输出 0，否则：
git submodule update --init --recursive

# 使用项目锁定的 ESP-IDF 版本（5.5.1）
unset IDF_PATH
unset IDF_PYTHON_ENV_PATH

./scripts/build_mpos.sh esp32s3
```

首次编译会下载 Xtensa 工具链，通常需要 **20–40 分钟**。

只修改 Python 文件时，可以增量重编：

```bash
MPOS_NO_CLEAN=1 ./scripts/build_mpos.sh esp32s3
```

**检查编译输出的最后三行**，应包含：

```text
=== Firmware size check (esp32s3) ===
Packed 1012.8 KiB into preload.img (vfs at 0x900000, 7.0 MiB)
Merged image: .../lvgl_micropy_ESP32_GENERIC_S3-SPIRAM_OCT-16.bin (16.0 MiB)
```

`internal_filesystem/` 会在编译时打进固件，因此修改其中的 Python 文件必须重新编译，不能通过往设备里拷贝文件来更新。

### 烧录

烧录 **完整的 16 MB 镜像**，写入地址 `0x0`。镜像已经包含 bootloader、分区表、otadata、app 和预加载文件系统。

**不需要执行 `erase_flash`。**

```bash
esptool.py --chip esp32s3 -p /dev/ttyACM0 -b 921600 \
  --before default_reset --after hard_reset \
  write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m \
  0x0 lvgl_micropy_ESP32_GENERIC_S3-SPIRAM_OCT-16.bin
```

- 镜像大小必须正好为 `16777216` 字节。
- Linux 串口权限：将用户加入 `dialout` 组后重新登录，或执行 `newgrp dialout`。

  ```bash
  sudo usermod -aG dialout $USER
  ```

- Fedora / Nobara 还需避免串口被占用：

  ```bash
  sudo systemctl mask --now brltty
  sudo systemctl stop ModemManager
  ```

查看日志：

```bash
tio /dev/ttyACM0 -b 115200
# 或 screen /dev/ttyACM0 115200
```

### 验收

预期显示初始化输出：

```text
AXS15231B: 320x480 qspi=True mode=FULL cf=7 swap=True chunks=10
```

- `qspi=True`：识别为 3.5B（I2C 探测 `0x20` 和 `0x3B`）。
- `mode=FULL`：全帧刷新生效；出现 `PARTIAL(strap fallback)` 表示降级。
- 屏幕保持常亮、不黑屏，触摸有响应。

### 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `Missing esp-mqtt submodule` | 嵌套子模块未初始化完整。在 `lvgl_micropython/lib/esp-idf` 中执行 `git submodule update --init --recursive`。 |
| `littlefs-python is required to build the preloaded filesystem` | 安装 `littlefs-python==0.19.0`。此时固件实际上尚未完成编译。 |
| `/dev/ttyACM0` 提示 `Permission denied` | 用户不在 `dialout` 组。执行 `newgrp dialout` 或重新登录。 |
| 串口 busy 或设备消失 | `brltty` 或 `ModemManager` 可能抢占串口，停止或 mask 对应服务。 |
| 烧录后反复重启 | 烧错镜像（例如 3.4 MB 的 app-only 镜像），或 Flash 中残留其他项目的分区表。重新烧录完整 16 MB 镜像。 |
| MicroPythonOS 部分页面返回时偶尔闪烁 | 已知的小问题，原因未知。 |

### 备注

- App 分区只剩 **68 KB（2%）** 余量，后续增加功能可能超出限制，需要裁剪组件或调整分区。
- 同一块板子在本固件和 ESP-IDF 项目之间切换时，必须烧录完整镜像（`0x0`），或执行 `idf.py flash --erase-all`。两者的分区布局不同。

### 致谢

- [MicroPythonOS](https://github.com/MicroPythonOS/MicroPythonOS) —— 操作系统本体。
- [lvgl_micropython](https://github.com/lvgl/lvgl_micropython) —— 构建系统。
- [straga](https://github.com/straga)、[de-dh](https://github.com/de-dh)。
  - [micropython_lcd – JC3248W535](https://github.com/straga/micropython_lcd/tree/master/device/JC3248W535) —— QSPI 初始化序列参考。
  - [ESP32-JC3248W535-Micropython-LVGL](https://github.com/de-dh/ESP32-JC3248W535-Micropython-LVGL) —— QSPI 初始化序列参考。
