# DOHC 工程工作流程与功能模块说明

## 1. 项目概述

DOHC 是一套运行在 Linux 嵌入式设备上的多路视觉数据采集与设备监控系统。系统同时接入三路 V4L2 彩色相机和一台 Intel RealSense T265，通过物理按键控制录制，并将图像、位姿和采集时间保存到 SD 卡。

工程由两个长期运行的核心进程组成：

- `system_monitor`：负责按键、LED、电池电压、芯片温度、Web 页面以及录制控制。
- `dohc`：负责相机发现、多路采集、帧批次组织和数据落盘。

两个进程通过本机 TCP 端口 `127.0.0.1:12345` 通信。监控进程是服务端，相机进程是客户端。

## 2. 总体工作流程

```text
设备启动
  │
  ├─ startup/startup_sdcard.sh
  │    └─ 检测并挂载 /dev/mmcblk1p1 到 /mnt/sdcard
  │
  └─ startup/startup_dohc.sh
       │
       ├─ 启动 system_monitor/system_monitor.py
       │    ├─ 启动 TCP 控制服务
       │    ├─ 启动 GPIO 按键监听
       │    ├─ 启动 ADC/温度监控
       │    ├─ 启动 LED 状态控制
       │    └─ 启动 Web/SSE 监控页面
       │
       ├─ 执行 dohc/setup.py
       │    └─ 扫描 V4L2 设备并生成 camera_config.json
       │
       └─ 启动 dohc/camera_startup.py
            ├─ 创建三路 V4L2 相机对象
            ├─ 创建 T265 相机对象
            ├─ 连接 monitor TCP 服务
            ├─ 启动相机采集线程
            ├─ 启动后台写盘线程
            └─ 进入 30 Hz 帧汇总循环
```

一次录制的状态变化如下：

```text
相机未就绪
  └─ camera 进入采集循环并发送 camera ready
       └─ 空闲状态（白色状态灯）
            └─ 单击按键
                 └─ monitor 发送 start data collection
                      └─ camera 创建存储会话并开始入队
                           └─ 采集中（蓝色状态灯）
                                └─ 双击按键
                                     └─ monitor 发送 end data collection
                                          └─ camera 停止入队并清空队列
                                               └─ 保存中（黄色状态灯）
                                                    └─ camera 关闭文件并发送 data saved
                                                         └─ 回到空闲状态
```

## 3. 功能模块清单

| 模块 | 主要功能 | 核心文件 | 运行方式 |
|---|---|---|---|
| SD 卡挂载 | 检测并挂载录制介质 | [`startup/startup_sdcard.sh`](startup/startup_sdcard.sh) | 独立 Shell 循环 |
| 系统启动 | 按顺序启动监控和相机进程 | [`startup/startup_dohc.sh`](startup/startup_dohc.sh) | Shell 主脚本 |
| 相机发现 | 扫描 V4L2 设备并生成设备映射 | [`dohc/setup.py`](dohc/setup.py) | 启动时执行一次 |
| 多路 V4L2 相机 | 采集主相机和左右 USB 相机 | [`dohc/camera_thread.py`](dohc/camera_thread.py) | 每台相机一个后台线程 |
| T265 模块 | 获取双鱼眼图像、位置、速度和姿态 | [`dohc/camera_thread.py`](dohc/camera_thread.py) | RealSense SDK 异步回调 |
| 帧汇总模块 | 按 30 Hz 取得各路最新数据并组成 batch | [`dohc/camera_startup.py`](dohc/camera_startup.py) | 相机进程主循环 |
| 帧队列模块 | 隔离实时采集与慢速磁盘 I/O | [`dohc/camera_startup.py`](dohc/camera_startup.py) | 有界线程安全队列 |
| JPEG 存储 | 五路图片目录和增量状态日志 | [`dohc/recording_writer.py`](dohc/recording_writer.py) | Writer 线程 + 图像线程池 |
| HDF5 存储 | 将每个帧批次写入单个 HDF5 文件 | [`dohc/utils.py`](dohc/utils.py) | Writer 线程 |
| 视频存储 | 五路 MP4 和对齐的 NPZ 状态数据 | [`dohc/recording_writer.py`](dohc/recording_writer.py) | Writer 线程 |
| TCP 事件通信 | 在 monitor 和 camera 之间传输控制与状态 | [`dohc/server_client.py`](dohc/server_client.py)、[`system_monitor/server_client.py`](system_monitor/server_client.py) | Socket 后台线程 |
| GPIO 按键 | 消抖、统计连击并触发开始/停止 | [`system_monitor/system_monitor.py`](system_monitor/system_monitor.py) | 独立 GPIO 线程 |
| LED 指示 | 显示录制、电池和过温状态 | [`system_monitor/led_control.py`](system_monitor/led_control.py) | 由监控线程调用 |
| ADC 电压监控 | 读取 ADC 并恢复电池输入电压 | [`system_monitor/system_monitor.py`](system_monitor/system_monitor.py) | 周期监控线程 |
| 温度监控 | 读取芯片温度并执行过温闪灯告警 | [`system_monitor/system_monitor.py`](system_monitor/system_monitor.py) | 独立温度线程 |
| Web 监控 | 展示设备状态、按键事件和相机日志 | [`system_monitor/system_monitor.py`](system_monitor/system_monitor.py) | HTTP + SSE |
| 相机测试 | 保存预览图片、JSON 报告和 HTML 页面 | [`dohc/test_cameras.py`](dohc/test_cameras.py) | 手动运行 |
| 硬件测试 | 测试温度、ADC、LED 和 GPIO | [`system_monitor/test_system_monitor.py`](system_monitor/test_system_monitor.py) | 手动运行 |

## 4. 多路摄像头模块

### 4.1 相机发现与角色分配

[`dohc/setup.py`](dohc/setup.py) 执行：

```bash
v4l2-ctl --list-devices
```

程序解析设备名称和第一个 `/dev/videoX` 节点，将结果写入 `camera_config.json`。这样设备号在重启后发生变化时，系统可以重新发现相机，而不必固定使用 `/dev/video0`。

[`dohc/camera_startup.py`](dohc/camera_startup.py) 根据名称和 USB 拓扑将设备分成：

- `main_camera`：DECXIN 主相机。
- `camera_left`：USB 左相机。
- `camera_right`：USB 右相机。

角色判断规则集中在 [`dohc/dohc_config.py`](dohc/dohc_config.py) 的 `USB_CAMERA_KEY_PREFIX` 和 `USB_LEFT_CAMERA_KEY_SUFFIX` 中。

### 4.2 V4L2 相机采集原理

[`CameraThread`](dohc/camera_thread.py) 使用 OpenCV 的 V4L2 后端打开相机，并设置 MJPG、分辨率和帧率：

```python
self.cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
```

正式采集默认关闭 OpenCV 的 RGB 转换，直接取得相机产生的 MJPEG 字节。程序根据 JPEG 的起止标志 `FF D8` 和 `FF D9` 截取完整图像。JPEG 存储模式下可以直接写入这些字节，省去一次解码和重新编码。

每台相机都有一个守护线程持续调用 `cap.read()`，但只保留最新帧：

```text
相机不断产生帧 → CameraThread.frame 始终被更新 → 主循环读取当前最新值
```

该模型优先保证实时性。主循环来不及读取时，中间帧会被覆盖，而不会无限积压。

### 4.3 T265 采集原理

[`T265Camera`](dohc/camera_thread.py) 开启三个 RealSense 数据流：

- 左鱼眼图像 `fisheye 1`
- 右鱼眼图像 `fisheye 2`
- 位姿 `pose`

RealSense SDK 在收到数据后调用 `_callback()`。图像回调更新左右鱼眼 NumPy 数组，Pose 回调整理以下状态：

| 字段 | 含义 |
|---|---|
| `position` | 三轴平移位置 |
| `velocity` | 三轴线速度 |
| `quaternion` | 旋转四元数 |
| `euler` | roll、pitch、yaw，单位为弧度 |
| `omega` | 三轴角速度 |
| `confidence` | T265 跟踪置信度 |

`get_frame()` 会复制 NumPy 图像后再返回，防止 SDK 回调在写盘期间修改同一块内存。

### 4.4 多路数据对齐方式

[`dohc/camera_startup.py`](dohc/camera_startup.py) 的主循环以 `SYSTEM_FPS=30` 运行，每轮读取各相机当前的最新值，并组成一个 batch：

```python
{
    "frame_main": frame_main,
    "frame_left": frame_left,
    "frame_right": frame_right,
    "t265": {
        "left": t265_left,
        "right": t265_right,
        "pose": pose
    },
    "capture_time_ns": time.time_ns()
}
```

同一个 batch 在写盘时共享一个 `frame_id`，从而实现软件层的数据对齐。该方法是“定期抽取各设备最新值”，不是硬件触发同步；对严格同步有要求时，还需要使用每台设备的原始时间戳进行配帧。

## 5. 帧队列与录制状态模块

### 5.1 为什么使用队列

相机采集要求稳定运行，而 SD 卡写入时间可能波动。项目使用：

```python
frame_queue = queue.Queue(maxsize=100)
```

将二者解耦：

```text
30 Hz 采集主循环 → frame_queue → writer_loop → SD 卡
```

队列满时使用 `put_nowait()` 立即失败并丢弃新 batch，不阻塞相机主循环。队列最大 100 个 batch，在 30 FPS 下约等于 3.3 秒的积压容量。

### 5.2 开始录制

相机进程收到 `start data collection` 后：

1. 根据当前时间生成会话名称。
2. 根据 `RECORDING_STORAGE_MODE` 创建存储后端。
3. 将 `frame_id` 清零。
4. 将 `record_state` 设置为 `True`。
5. 后续主循环开始向 `frame_queue` 放入 batch。

### 5.3 停止录制

相机进程收到 `end data collection` 后：

1. 先令 `record_state=False`，禁止产生新 batch。
2. 启动 finalizer 收尾线程。
3. 使用 `frame_queue.join()` 等待已有 batch 全部写完。
4. 关闭 HDF5、MP4 或 JPEG Writer。
5. 向 monitor 发送 `data saved`。

这种顺序能够避免用户停止录制时直接丢失队列尾部的数据。

## 6. 数据存储模块

存储模式由 [`dohc/dohc_config.py`](dohc/dohc_config.py) 中的 `RECORDING_STORAGE_MODE` 决定，当前默认值是 `jpeg`。

### 6.1 JPEG 模式

[`JpegDirectoryWriter`](dohc/recording_writer.py) 为一次录制创建：

```text
/mnt/sdcard/<session>/
├─ cam0/          # 主相机
├─ cam1/          # 左 USB 相机
├─ cam2/          # 右 USB 相机
├─ t265_left/     # T265 左鱼眼
├─ t265_right/    # T265 右鱼眼
└─ states.jsonl   # 每帧的时间戳和 Pose
```

五路图像使用相同文件编号，例如 `cam0/25.jpg`、`cam1/25.jpg` 和 `t265_left/25.jpg` 都对应 `frame_id=25`。

如果某一路暂时没有图像，Writer 会写一张黑色占位图，而不是跳过编号。Pose 缺失时写入 `null`。这样五路数据的编号始终保持一致。

V4L2 相机的原始 JPEG 直接写入文件；T265 的灰度 NumPy 图像由 `cv2.imwrite()` 编码。五张图使用线程池并行保存，Pose 则通过独立状态队列增量写入 `states.jsonl`。

### 6.2 HDF5 模式

[`dohc/utils.py`](dohc/utils.py) 将每个 batch 保存成一个 group：

```text
frame_0/
├─ cam0
├─ cam1
├─ cam2
└─ t265/
   ├─ left
   ├─ right
   └─ pose 属性
```

图像以 JPEG 编码后的 `uint8` 数组保存，Pose 以 JSON 字符串属性保存。优点是一次录制集中在单个文件中，缺点是异常断电时单文件受损的影响可能更大。

### 6.3 Video 模式

[`VideoRecordingWriter`](dohc/recording_writer.py) 输出五个 MP4 文件和一个状态文件：

```text
<session>_cam0.mp4
<session>_cam1.mp4
<session>_cam2.mp4
<session>_t265_left.mp4
<session>_t265_right.mp4
<session>_states.npz
```

缺失图像使用黑帧补齐，从而保证五个视频帧数相同。状态数据先保存在内存中，在 Writer 关闭时统一生成压缩 NPZ。

当前 V4L2 正式采集默认返回原始 JPEG 字节，而 Video Writer 需要解码后的 NumPy 图像。因此直接切换到 `video` 模式前，需要在采集端关闭 `raw_mjpeg`，或在 Video Writer 写入前增加 JPEG 解码。

## 7. TCP 事件通信模块

[`dohc/server_client.py`](dohc/server_client.py) 和 [`system_monitor/server_client.py`](system_monitor/server_client.py) 当前是内容相同的 Socket 封装。

默认协议为：

```text
4 字节大端无符号整数（消息长度） + Pickle 序列化消息
```

主要事件如下：

| 方向 | 事件 | 含义 |
|---|---|---|
| monitor → camera | `start data collection` | 创建会话并开始录制 |
| monitor → camera | `end data collection` | 停止入队并开始收尾 |
| camera → monitor | `camera ready` | 相机已进入采集主循环 |
| camera → monitor | `data saved` | 队列已清空且存储已关闭 |
| camera → monitor | `{"type": "camera_log"}` | 将相机日志转发到网页 |

监控进程只有收到 `camera ready` 后才接受按键开始/停止操作，避免相机未启动时误触发录制。

## 8. GPIO 按键模块

GPIO 参数定义在 [`system_monitor/config.py`](system_monitor/config.py)：

```python
GPIO_CHIP = "/dev/gpiochip1"
GPIO_LINE = 6
GPIO_EDGE = "rising"
DEBOUNCE_TIME = 0.2
CLICK_TIMEOUT = 1.0
```

[`gpio_task()`](system_monitor/system_monitor.py) 等待 GPIO 边沿事件。一个边沿到达后：

1. 与上次有效边沿间隔小于 0.2 秒时，认为是机械抖动并忽略。
2. 否则将 `click_count` 加一。
3. 最后一次边沿后 1 秒没有新事件时，认为本轮连击结束。
4. 调用 `handle_click_event(click_count)`。

当前动作映射：

| 点击次数 | 动作 |
|---|---|
| 单击 | 开始数据采集 |
| 双击 | 停止采集并保存 |
| 三击 | 只记录和推送事件，未绑定业务动作 |
| 更多 | 只记录连击次数 |

## 9. LED 指示模块

[`system_monitor/led_control.py`](system_monitor/led_control.py) 通过 `/dev/ws2812_new` 控制 WS2812。程序把 GPIO 芯片编号、GPIO 线号、LED 编号和 RGB 颜色打包成 16 字节二进制结构，再写入设备驱动。

系统使用两颗逻辑指示灯：

### 9.1 状态 LED

| 颜色 | 状态 |
|---|---|
| 熄灭 | 相机尚未就绪 |
| 白色 | 相机已就绪，当前空闲 |
| 蓝色 | 正在采集 |
| 黄色 | 停止采集后正在清空队列和保存 |

### 9.2 电池 LED

| 条件 | 颜色 |
|---|---|
| 电压 ≥ 7.0 V | 绿色 |
| 6.5 V ≤ 电压 < 7.0 V | 黄色 |
| 电压 < 6.5 V | 红色 |
| 芯片温度 > 80°C | 红色闪烁 |

`set_indicator_led()` 会缓存每颗 LED 的最后颜色。目标颜色没有变化时不会重复写驱动，以减少不必要的设备操作。

## 10. ADC 电压与温度模块

### 10.1 电池电压

监控进程从 Linux IIO sysfs 读取：

```text
/sys/bus/iio/devices/iio:device0/in_voltage3_raw
/sys/bus/iio/devices/iio:device0/in_voltage_scale
```

计算过程：

```python
adc_voltage = raw * scale / 1000.0
vcc_voltage = adc_voltage * VOLTAGE_DIVIDER_RATIO
```

第一步得到 ADC 引脚电压，第二步根据硬件分压比例恢复实际输入电压。监控线程默认每 20 秒采集一次，并更新电池 LED 和 Web 页面。

### 10.2 芯片温度

温度从以下节点读取：

```text
/sys/class/thermal/thermal_zone0/temp
```

节点值除以 1000 后得到摄氏度。独立温度线程每秒检查一次；超过阈值后接管电池 LED，以 1 Hz 周期执行红色/熄灭闪烁。温度恢复后，再根据最近一次电池电压恢复 LED 颜色。

## 11. Web 监控模块

[`system_monitor/system_monitor.py`](system_monitor/system_monitor.py) 在 `0.0.0.0:8080` 提供：

- `/`：返回内嵌的监控 HTML 页面。
- `/events`：建立 Server-Sent Events 长连接。

SSE 数据包括：

- ADC 原始值和换算电压。
- 电池电压。
- 芯片温度。
- GPIO 原始点击计数。
- 单击、双击等控制事件。
- 相机就绪和保存完成状态。
- 相机进程运行日志。

浏览器通过以下地址访问：

```text
http://<设备IP>:8080
```

## 12. 线程与并发模型

### 12.1 相机进程

```text
camera_startup 主线程        30 Hz 汇总最新帧并入队
├─ V4L2 主相机线程           持续更新最新主相机帧
├─ V4L2 左相机线程           持续更新最新左相机帧
├─ V4L2 右相机线程           持续更新最新右相机帧
├─ RealSense SDK 回调线程     更新双鱼眼和 Pose
├─ Socket 接收线程            接收开始/停止命令
├─ Writer 线程                消费 frame_queue
├─ JPEG 图像线程池            并行写五路图片
├─ JPEG 状态线程              增量写 states.jsonl
└─ Finalizer 线程             停止时等待队列并关闭存储
```

### 12.2 监控进程

```text
system_monitor 主线程        维持进程运行
├─ 系统信息线程               周期读取 ADC 和温度
├─ GPIO 线程                  监听按键和判断连击
├─ Web 服务线程               提供 HTML 与 SSE
├─ 温度告警线程               控制过温闪灯
├─ Socket accept 线程         接受相机连接
└─ Socket 客户端处理线程      处理相机状态和日志
```

## 13. 测试模块

### 13.1 相机测试

运行示例：

```bash
python3 dohc/test_cameras.py --frames 5
python3 dohc/test_cameras.py --frames 10
python3 dohc/test_cameras.py --skip-realsense
```

测试程序会为每台相机保存预览图片和 JSON 结果，并生成 `index.html` 供浏览器检查。该测试属于硬件集成测试，不会启动正式的录制控制流程。

### 13.2 系统硬件测试

运行示例：

```bash
python3 system_monitor/test_system_monitor.py --skip-led
python3 system_monitor/test_system_monitor.py --led-count 5
python3 system_monitor/test_system_monitor.py --watch-gpio 10
```

测试内容包括温度节点、ADC、电压换算、WS2812 写入以及 GPIO 边沿事件。

## 14. 核心配置速查

| 配置 | 当前值 | 作用 |
|---|---:|---|
| `SYSTEM_FPS` | `30.0` | 软件帧汇总频率 |
| `MAX_FRAME_QUEUE_SIZE` | `100` | 最大待写 batch 数量 |
| `DEFAULT_H5F_FOLDER` | `/mnt/sdcard/` | 录制输出根目录 |
| `RECORDING_STORAGE_MODE` | `jpeg` | 当前存储后端 |
| `ENABLE_T265_CAMERA` | `True` | 是否启用 T265 |
| `EVENT_SERVER_PORT` | `12345` | monitor/camera 控制端口 |
| `WEB_PORT` | `8080` | Web 监控端口 |
| `CLICK_TIMEOUT` | `1.0 s` | 连击结束判断时间 |
| `DEBOUNCE_TIME` | `0.2 s` | 按键软件消抖时间 |
| `BATTERY_LOW_THRESHOLD` | `6.5 V` | 电池红灯阈值 |
| `BATTERY_HIGH_THRESHOLD` | `7.0 V` | 电池绿灯阈值 |
| `OVER_TEMPERATURE_THRESHOLD_C` | `80°C` | 过温告警阈值 |

## 15. 部署和维护注意事项

1. 项目依赖 Linux 的 V4L2、IIO、GPIO、thermal sysfs 和自定义 WS2812 驱动，不能在普通 Windows 环境完整运行。
2. 正式启动前应确保 SD 卡已经挂载到 `/mnt/sdcard`，否则录制后端无法创建输出。
3. 相机角色依赖设备名称和 USB 拓扑。更换接口或相机后，需要检查 `USB_LEFT_CAMERA_KEY_SUFFIX`。
4. 当前多路对齐属于软件定频抽样，不应当视为严格的硬件同步。
5. 队列满时会丢弃新 batch。出现相关日志时，应检查 SD 卡性能、JPEG 写入耗时和系统负载。
6. `video` 模式与当前原始 MJPEG 采集格式不直接兼容，启用前需要增加解码步骤或改变 V4L2 输出方式。
7. camera 对 monitor 的控制连接在启动阶段只尝试一次；如果启动顺序异常或 monitor 中途重启，需要重启 camera 或补充自动重连机制。
8. TCP 默认使用 Pickle 序列化，只应当用于可信的本机通信。
9. `dohc/server_client.py` 和 `system_monitor/server_client.py` 是重复实现，后续可以合并为共享模块。
10. Web 服务当前使用单线程 `HTTPServer`，SSE 长连接较多时建议改为 `ThreadingHTTPServer`。

## 16. 建议阅读顺序

如果需要继续开发或调试，推荐按以下顺序阅读源码：

1. [`dohc/dohc_config.py`](dohc/dohc_config.py)：理解相机、存储和通信配置。
2. [`startup/startup_dohc.sh`](startup/startup_dohc.sh)：理解两个主进程的启动顺序。
3. [`system_monitor/system_monitor.py`](system_monitor/system_monitor.py)：理解按键如何产生控制事件。
4. [`dohc/setup.py`](dohc/setup.py)：理解相机设备如何发现和分类。
5. [`dohc/camera_thread.py`](dohc/camera_thread.py)：理解 V4L2 和 T265 如何提供最新数据。
6. [`dohc/camera_startup.py`](dohc/camera_startup.py)：理解录制状态、帧 batch、队列和收尾流程。
7. [`dohc/recording_writer.py`](dohc/recording_writer.py)：理解 JPEG 和视频数据布局。
8. [`dohc/utils.py`](dohc/utils.py)：理解 HDF5 数据布局。
9. [`dohc/test_cameras.py`](dohc/test_cameras.py) 和 [`system_monitor/test_system_monitor.py`](system_monitor/test_system_monitor.py)：理解硬件验证方法。
