# monitor_test

本文件说明 `monitor_test.py` 的用途、依赖与运行方式。

## 简要说明
`monitor_test.py` 同时输出 ICM-42688（IMU）的六轴数据与摄像头外部触发测得的帧率，并可保存抓取的帧。

主要功能：
- 初始化并读取 ICM-42688（通过 SPI），打印温度/加速度/陀螺数据。
- 使用 OpenCV 从 V4L2 摄像头拉流，测量并打印实际帧率与读取错误数。
- 在启动拉流后调用 `v4l2-ctl` 设置 `backlight_compensation=1`。
- 可选择显示预览或在后台运行并保存帧到 `camera2/` 目录。

## 前置依赖
- Python 3.8+
- Python 包：`opencv-python`, `periphery`（用于 SPI），`numpy`（间接可能需要）
- 系统工具：`v4l2-ctl`（用于设置摄像头控制项）

安装示例：

```bash
python -m pip install --upgrade pip
pip install opencv-python pyftdi periphery
```

注意：脚本使用 `/dev/spidev*` 与 `v4l2` 接口，主要在 Linux（嵌入式或开发板）环境运行。在 Windows 上请使用 WSL 或在 Linux 主机上执行。

## 命令行参数
脚本内使用 argparse，主要参数如下：

- `--device`：摄像头设备，默认 `/dev/video0`
- `--spi`：SPI 设备，默认 `/dev/spidev0.0`
- `--speed`：SPI 速率（Hz），默认 `1000000`
- `--width`：帧宽，默认 `1280`
- `--height`：帧高，默认 `480`
- `--fps`：期望帧率，默认 `210`
- `--delay`：启动拉流后等待多少秒再设置 BLS（backlight_compensation），默认 `0.5`
- `--no-preview`：不显示 OpenCV 窗口（后台运行模式）

更多细节请在命令行运行 `python monitor_test.py --help`。

## 运行示例

在连接好相机与 IMU 后运行：

```bash
# 在终端中显示预览与状态
python monitor_test.py --device /dev/video0 --spi /dev/spidev0.0

# 后台运行并不显示预览（推荐用于远程或自动化测试）
python monitor_test.py --no-preview --device /dev/video0 --spi /dev/spidev0.0
```

脚本会在每秒打印一次 IMU 数据与测量帧率，并在有新帧时将图像保存到 `camera2/image_*.jpg`（请确保存在 `camera2/` 目录或创建它）。

## 调试与注意事项
- 确认 SPI 设备路径正确且当前用户有访问权限（可能需要 root 或加入 `spi` 组）。
- `v4l2-ctl` 必须可用以设置 `backlight_compensation`；部分摄像头或驱动可能不支持该控制项。
- 如果无法打开摄像头，请检查设备节点、驱动与权限，并尝试降低分辨率或帧率。
- 在出现时间同步或触发问题时，使用示波器或逻辑分析仪验证外部触发线信号。

## 反馈
如需我把 `monitor_test.py` 中的参数说明添加为更详细的示例（包括常见设备路径、ffmpeg 替代方案或 Windows 兼容建议），回复告诉我想要的详情。
# ICM-42688-P 实物板分阶段测试

## 接线

测试自制板时断开外部 HJ0108。板载 TXU0304 负责 3.3 V 与 1.8 V SPI 电平转换。

```text
LubanCat4 GND       -> 板卡 GND
LubanCat4 3.3V      -> 板卡 +3V3_MCU / TXU0304 VCCA
LubanCat4 SPI_SCK   -> MCU_SPI_SCK
LubanCat4 SPI_MOSI  -> MCU_SPI_MOSI
LubanCat4 SPI_MISO  <- MCU_SPI_MISO
LubanCat4 SPI_CS_N  -> MCU_SPI_CS_N
LubanCat4 Pin 33 PWM-> MCU_HEAT_PWM

独立 5 V 电源正极   -> 板卡 +5V
独立 5 V 电源负极   -> 板卡 GND
```

不要把 HJ0108 的 1.8 V 输出连接到板载 `+1V8_IMU`。不要同时并联 LubanCat4 5 V 与独立 5 V 电源。

## 环境准备

```bash
sudo python3 -m pip install python-periphery
ls -l /dev/spidev*
ls -l /sys/class/pwm/
```

SPI 和 PWM 必须先在 LubanCat4 的设备树/Pinmux 中启用。以下命令中的 `/dev/spidevX.Y`需替换为实际节点。

## 顺序

### 1. 断电检查

- 5 V 对 GND 不短路。
- 1.8 V 对 GND 不短路。
- Q1 Gate 对 GND 约 100 kΩ。
- 八颗 330 Ω 并联等效值约 41.25 Ω。

### 2. 限流上电

- 外部电源设 5.0 V、50 mA 限流。
- PWM 保持低。
- 测得板上 1.8 V 正常后，把限流提高到 250 mA。
- TXU0304 VCCA 应为 3.3 V，VCCB 应为 1.8 V。

### 3. 识别器件

```bash
sudo python3 hardware_test/icm42688_bringup.py \
  --spi /dev/spidevX.Y id
```

期望：

```text
WHO_AM_I = 0x47
PASS: SPI communication and device identity
```

### 4. 温度与六轴数据

PCB 静止水平放置：

```bash
sudo python3 hardware_test/icm42688_bringup.py \
  --spi /dev/spidevX.Y stream --duration 20 --rate 10
```

期望：

- 温度处于合理室温范围，连续且不为 `-32768`。
- 静止时陀螺三轴接近 0 dps。
- 加速度合向量接近 1 g。
- 翻转板卡时，对应加速度轴符号变化。

### 5. INT1

```bash
sudo python3 hardware_test/icm42688_bringup.py \
  --spi /dev/spidevX.Y interrupt --duration 10
```

用示波器测 INT1，期望约 200 个脉冲/秒。若 ICM VDDIO 为 1.8 V，必须确保 INT1 已经转换到 MCU 可识别的电平。

### 6. 10% 开环加热

先用示波器确认 `pwmchip3/pwm0`确实对应 Pin 33：

```bash
sudo python3 hardware_test/icm42688_bringup.py \
  --spi /dev/spidevX.Y heater \
  --pwmchip 3 --channel 0 \
  --duty 0.10 --duration 30 --hard-limit 45
```

期望：

- 5 V平均新增电流约 12 mA。
- ICM内部温度缓慢上升。
- 30秒后PWM自动关闭。
- 八颗电阻发热基本对称。

之后可测试 25%，仍限定时间：

```bash
sudo python3 hardware_test/icm42688_bringup.py \
  --spi /dev/spidevX.Y heater \
  --duty 0.25 --duration 30 --hard-limit 50
```

### 7. 35°C闭环

```bash
sudo python3 hardware_test/icm42688_bringup.py \
  --spi /dev/spidevX.Y closed-loop \
  --target 35 --max-duty 0.50 \
  --duration 300 --hard-limit 50
```

期望温度向 35°C收敛。由于内部温度传感器存在绝对偏差，需要用外部温度计核对真实温度。35°C测试稳定后，才能把目标逐步提高到 40°C、45°C。

## 安全

- 所有加热命令都有运行时间和温度硬限制。
- SPI失败、温度无效或超温时脚本会进入 `finally`并关闭PWM。
- `kill -9`、系统掉电或内核故障不能保证执行清理，因此电源限流和Q1栅极下拉必须有效。
- 首板加热期间不要无人值守。
