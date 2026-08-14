# ICM-42688-P SPI 接口测试总结

## 1. 文档目的和适用硬件

本文用于测试 LubanCat 4 与 IMU6 测试板上 ICM-42688-P 的四线 SPI 通信，并记录当前测试现象、判定方法和故障定位顺序。

本文依据已经检查过的 IMU6 原理图和 PCB：

- 板卡从 `+5V` 经 AMS1117-3.3 产生 `+3.3V_IMU`。
- ICM-42688-P 的 VDD 和 VDDIO 均为 3.3 V。
- LubanCat 4 的 SPI I/O 为 3.3 V，因此当前板卡的 SPI 应直接连接，不接 HJ0108，也不需要 1.8 V 电平转换。
- 如果实际测试的是带 TXU0304 或 1.8 V VDDIO 的另一版 PCB，不能直接套用本文接线，必须重新核对电源域和方向控制。

## 2. 已核对的原理图和 PCB 连接

| 板卡接口 | 网络 | ICM 引脚 | 功能 |
|---|---|---:|---|
| H1-1 | `SPI_SCK` | U1-13 | SPI 时钟输入 |
| H1-2 | `SPI_MOSI` | U1-14 | SPI 数据输入 |
| H1-3 | `SPI_CS` | U1-12 | 低有效片选输入 |
| H1-4 | `SPI_MISO` | U1-1 | SPI 数据输出 |
| H2-1 | `+5V` | U2-3 | 板卡电源输入 |
| H2-2 | `GND` | U1-6/U1-7 | 公共地 |

U1-5（VDDIO）和 U1-8（VDD）连接 3.3 V，U1-7 按手册接地。工程数据中四条 SPI 铜线均从 H1 连通至 U1，网络名、过孔和 U1 封装编号未发现明显接反。

四条线在 PCB 中约为 13.56 mm。蛇形等长不是低速 SPI 的必要条件，但本身不会造成当前完全无响应。

## 3. LubanCat 4 接线

使用 `/dev/spidev0.0`（CS0）时：

```text
LubanCat 物理 23 脚 SCLK  -> H1-1 SPI_SCK
LubanCat 物理 19 脚 MOSI  -> H1-2 SPI_MOSI
LubanCat 物理 24 脚 CS0   -> H1-3 SPI_CS
LubanCat 物理 21 脚 MISO  <- H1-4 SPI_MISO
LubanCat 任一 GND          -> H2-2 GND
+5 V 电源                  -> H2-1 +5V
```

使用 `/dev/spidev0.1` 时，H1-3 必须改接 LubanCat 的 CS1（物理 26 脚）。`spidev0.0` 和 `spidev0.1` 的区别只在片选，不能在 H1-3仍接 CS0 时仅靠改软件设备名获得正确通信。

只允许一个 5 V 电源给板卡供电。若用独立台式电源，应把其负极与 LubanCat GND 共地，不要再并联 LubanCat 的 5 V 引脚。

## 4. 测试环境

```bash
sudo python3 -m pip install python-periphery
ls -l /dev/spidev*
ls -l /sys/kernel/debug/pinctrl/pinctrl-rockchip-pinctrl/pinmux-pins
```

预期至少出现：

```text
/dev/spidev0.0
/dev/spidev0.1
```

设备节点存在只说明 SPI 控制器和设备树已启用，不代表外部 ICM 已经通信。

## 5. 正确测试顺序

### 5.1 断电检查

断开 LubanCat 和所有电源后测量：

1. H2-1（5 V）对 GND 不应接近 0 Ω。
2. 3.3 V 对 GND 不应接近 0 Ω。
3. H1 四个信号之间不应短路。
4. 检查以下端到端通断，正常应接近 0 Ω，通常不超过几欧：

```text
H1-1 <-> U1-13  SCLK
H1-2 <-> U1-14  MOSI
H1-3 <-> U1-12  CS
H1-4 <-> U1-1   MISO
```

U1 为 LGA 封装，可测对应焊盘引出的第一段铜线或最近过孔。不要把二极管档约 0.5 V 的压降当作短路结论，应使用断电电阻档确认。

### 5.2 限流上电和电源确认

建议台式电源先设为 5.0 V、50 mA 限流。确认没有异常发热后再提高限流。

使用示波器或万用表测量：

```text
H2-1                 约 5.0 V
U2 输出              约 3.3 V
U1-5 VDDIO           约 3.3 V
U1-8 VDD             约 3.3 V
```

U2 输出正常并不能证明 U1 焊盘处供电正常，必须尽量靠近 U1 测量。示波器还应检查上电时是否存在明显跌落或振荡。

### 5.3 控制器回环测试

回环测试只用于验证 LubanCat SPI 控制器、设备树和针脚复用：

1. 完全断开 IMU6 板的四条 SPI 线。
2. 只在 LubanCat 端把 MOSI（物理19脚）与 MISO（物理21脚）短接。
3. 运行原始路径测试：

```bash
sudo python3 hardware_test/spi_path_test.py \
  --spi /dev/spidev0.0 \
  --speed 100000 \
  --mode 0 \
  --count 3
```

短接时预期 `RX=TX`；拆除回环线后通常为 `FF FF`。如果连接 ICM 后仍对任意发送内容都得到 `RX=TX`，这不是通信成功，而是 MOSI 到 MISO 存在外部短路、接线交叉或板上回授路径。

### 5.4 读取芯片身份

拆除回环线，按第3节连接 IMU6 板，然后运行：

```bash
sudo python3 hardware_test/icm42688_test.py \
  --spi /dev/spidev0.0 \
  --speed 100000 \
  id
```

`WHO_AM_I` 寄存器地址是 `0x75`。读操作将地址最高位置1，所以发送的首字节是：

```text
0x75 | 0x80 = 0xF5
```

正常事务为：

```text
TX = F5 00
RX = xx 47
WHO_AM_I = 0x47
PASS: SPI communication and device identity
```

第一个接收字节发生在发送地址期间，可忽略；第二个字节 `0x47` 才是 ICM-42688-P 的固定身份值。

### 5.5 连续数据测试

只有 `id` 通过后才能继续：

```bash
sudo python3 hardware_test/icm42688_test.py \
  --spi /dev/spidev0.0 \
  --speed 1000000 \
  stream --duration 20 --rate 10
```

合格判据：

- 温度连续、处于合理范围且不为无效值 `-32768`。
- 静止时三轴角速度接近 0 dps。
- 静止时三轴加速度合向量接近 1 g。
- 转动或翻转板卡时，对应数据随姿态变化。

## 6. SPI 原始结果判读

| 接收结果 | 含义 | 优先排查 |
|---|---|---|
| `xx 47` | 正常读取身份寄存器 | 可继续数据测试 |
| `FF FF` | MISO 高电平或悬空，没有从机有效响应 | CS、U1供电、MISO开路、U1焊接 |
| `00 00` | MISO被拉低或芯片未正常驱动 | MISO对地短路、供电、焊接 |
| `RX=TX` | MOSI信号反馈到了MISO | 外部回环、MOSI/MISO短路或接错 |
| 随机变化 | 信号完整性、共地、接触不良或模式错误 | 示波器、降低频率、缩短飞线 |

把速度从 10 kHz 改到 1 MHz后结果始终完全相同，通常说明问题不是“频率太高”。Linux控制器不接受过低的100 Hz SPI速度并返回 `EINVAL`，也不代表脚本寄存器地址有问题。

## 7. 示波器定位方法

必须尽量在 U1 附近测量，而不能只测 H1：

1. CS空闲应为高，传输 `F5 00` 的整个16位期间应保持低。
2. SCLK应出现16个完整脉冲；首先使用 Mode 0、100 kHz。
3. MOSI应能解码出 `F5 00`。
4. MISO在第二个字节期间应输出 `47`，不能始终保持高电平。

推荐同时观察：

```text
CH1 = CS
CH2 = SCLK
CH3 = MOSI
CH4 = MISO
```

判断逻辑：

- H1有波形、U1焊盘没有：PCB铜线、过孔、连接器或实物开路。
- CS/SCLK/MOSI均到达U1，但MISO始终高：优先判断U1-1虚焊、芯片未启动或芯片损坏。
- U1-1已有数据，H1-4没有：MISO走线或过孔开路。

## 8. 当前已观察到的现象和结论

已经出现过以下结果：

- 连接某种路径时，任意发送内容均得到 `RX=TX`，说明当时存在MOSI到MISO的回授。
- 拔掉MISO或四线全部断开后得到 `FF FF`，符合MISO浮空的表现。
- 调换MOSI和SCK后得到 `00 00`，不代表接线正确。
- Mode 0和Mode 3、10 kHz至1 MHz范围内均未得到 `0x47`。
- U2的5 V输入和3.3 V输出已实测正常。
- 对EDA工程的检查未发现SPI引脚交换或PCB网络断开。

综合判断：原理图和PCB没有发现致命逻辑错误，当前最大风险在实物装配，特别是 U1-1、U1-12、U1-13、U1-14 的LGA焊点。U1-3是保留脚，焊盘脱落本身可以悬空，但返修过程可能导致芯片偏移、相邻焊点受损或器件热损伤。

如果示波器确认CS、SCLK和MOSI都已到达U1，而MISO仍无输出，停止继续修改频率，优先重新焊接或更换U1。

## 9. PCB下一版改进项

- 把VDDIO的10 nF以及VDD的100 nF、2.2 µF去耦电容放到U1电源脚附近。
- CS增加约10 kΩ上拉到VDDIO，避免主机启动期间悬空。
- U1-9若不使用INT2/FSYNC/CLKIN，按手册接地或预留下拉位置。
- SPI短直走线、减少过孔；不需要蛇形严格等长。
- 给CS、SCLK、MOSI、MISO和U1电源预留可接示波器的测试点。
- 量产前做LGA X-ray或至少抽样切片/返修验证，确认焊膏量和回流曲线。

## 10. 测试记录模板

```text
日期：
PCB版本/序列号：
LubanCat镜像/内核：
spidev节点：
SPI模式/频率：
5V实测：
U1 VDD实测：
U1 VDDIO实测：
CS波形：
SCLK波形：
MOSI解码：
MISO解码：
WHO_AM_I：
结论：PASS / FAIL
故障位置及处理：
```

