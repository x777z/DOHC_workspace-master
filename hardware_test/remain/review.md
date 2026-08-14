
# DOHC项目代码总结整理

# 1.项目概述

DOHC 是一套运行在Linux嵌入式设备上的多路视觉数据采集与设备监控系统。该系统同时接入三路V4L2彩色相机和一台Intel RealSense T265相机，通过物理按键控制开始/停止，并按照不同模式将图像、位姿、采集时间等相关信息保存到SD卡，通过网页查看电压、温度、按键和相机日志

项目由两个长期运行的核心进程组成：
 `system_monitor`：负责按键、LED、电池电压、芯片温度、Web 页面以及录制控制
 `dohc`：负责相机发现、多路采集、帧批次组织和数据落盘
两个进程通过本机 TCP 端口 `127.0.0.1:12345` 通信，监控进程是服务端，相机进程是客户端

# 2.总体工作流程

该项目的工作流程如下：

设备启动
  │
  ├─ startup/startup_sdcard.sh
  │    └─ 挂载 /dev/mmcblk1p1 到 /mnt/sdcard，并循环检测/dev/mmcblk1p1
  │
  └─ startup/startup_dohc.sh 
      └─ 先启动监控进程，等待 1 秒，再扫描相机并启动采集进程
       │
       ├─ 启动 system_monitor/system_monitor.py
       │    ├─ 启动 TCP 控制服务，实现本机与“dohc/camera_startup.py”双向通信
       │    ├─ 启动 GPIO 按键监听，将单击/双击转换为开始/停止采集命令
       │    ├─ 启动 ADC/温度监控，读取ADC电压和芯片温度，并通过SSE推送到监控网页
       │    ├─ 启动 LED 状态控制，根据相机、录制、电池和温度状态控制两颗WS2812指示灯
       │    └─ 启动 Web/SSE监控页面
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

一次录制的状态变化如下：

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

# 3.各功能模块说明

## 3.1 系统启动模块 starup
startup_dohc.sh ：

该脚本用于在 Linux 服务器上依次启动监控进程和相机采集进程，并将它们的输出分别记录到带有时间戳的日志文件中
先激活 system_monitor 环境，运行系统监控脚本
等待 1 秒
切换到 dohc 环境，运行一个初始化 setup.py，然后启动摄像头相关的服务脚本
最终保持脚本运行，等待两个后台服务结束（通常它们会一直运行，所以脚本会常驻）
这样设计保证两个服务在不同的 conda 环境中运行，互不干扰，并将标准输出和错误输出分别重定向到带时间戳的日志文件

startup_sdcard.sh：

该脚本负责在嵌入式或服务器环境中自动管理 SD 卡的挂载
工作流程为：
通过环境变量或默认值配置设备路径、挂载点和检查间隔，确保挂载点目录存在
进入无限循环，每隔 2 秒检测一次 /dev/mmcblk1p1：
如果 SD 卡已经挂载，则跳过本次挂载尝试，继续等待
如果 SD 卡设备存在且尚未挂载，则执行挂载到 /mnt/sdcard，并输出结果
如果设备不存在，则静默跳过，等待下一次检查

## 3.2 GPIO 按键模块 

GPIO 参数定义在 [`system_monitor/config.py`]：

GPIO_CHIP = "/dev/gpiochip1"
GPIO_LINE = 6  #在gpiochip1使用的具体引脚编号
GPIO_EDGE = "rising"  #上升沿
DEBOUNCE_TIME = 0.2  #按键消抖时间
CLICK_TIMEOUT = 1.0  #按键结束判定的超时时间

gpio_task()函数:
监听 GPIO 边沿，先软件消抖，再等待超时以判断连击次数

当一个边沿到达后：
与上次有效边沿间隔小于 0.2 秒时，认为是机械抖动并忽略
否则将 `click_count` 加一
最后一次边沿后 1 秒没有新事件时，认为本轮连击结束

动作映射：
| 点击次数 | 动作 |
|---|---|
| 单击 | 开始数据采集 |
| 双击 | 停止采集并保存数据 |
| 三击 | 只记录三击事件，未绑定动作 |
| 更多 | 只记录连击次数，未绑定动作 |

## 3.3 ADC电压模块

ADC计算：
voltage = raw * scale / 1000.0
vcc_out_voltage = voltage * VOLTAGE_DIVIDER_RATIO
第一步把 ADC 原始值换算成 ADC 引脚电压，第二步根据分压比恢复输入电压，然后根据电压控制电池LED灯

## 3.4 LED 指示模块 

显示录制、电池和过温状态
通过“/dev/ws2812_new”控制 WS2812。程序把 GPIO 芯片编号、GPIO 线号、LED 编号和 RGB 颜色打包成 16 字节二进制结构，写入设备驱动

系统使用两颗逻辑指示灯：
电池 LED：
定义update_battery_led(vcc_voltage)函数

| 条件 | 颜色 |
|---|---|
| 电压 ≥ 7.0 V | 绿色 |
| 6.5 V ≤ 电压 < 7.0 V | 黄色 |
| 电压 < 6.5 V | 红色 |
| 芯片温度 > 80°C | 红色闪烁 |

set_indicator_led() 会缓存每颗LED的最后颜色，目标颜色没有变化时不会重复写驱动

状态 LED：
def set_collecting_led():
    set_indicator_led(config.STATUS_LED, config.LED_BLUE, "collecting data")

def set_saving_led():
    set_indicator_led(config.STATUS_LED, config.LED_YELLOW, "saving data")

def set_idle_led():
    set_indicator_led(config.STATUS_LED, config.LED_WHITE, "data collection ended")

def set_status_led_off():
    set_indicator_led(config.STATUS_LED, config.LED_OFF, "camera is not ready")

| 颜色 | 状态 |
|---|---|
| 熄灭 | 相机尚未就绪 |
| 白色 | 数据采集结束，回到空闲状态 |
| 蓝色 | 正在采集数据 |
| 黄色 | 停止采集后正在保存数据 |

## 3.5 温度监控模块

温度从以下节点读取：
/sys/class/thermal/thermal_zone0/temp

将该节点值除以 1000 后得到摄氏度，独立温度线程每秒检查一次
当芯片温度过高，超过阈值（80℃）时，接管电池 LED，电池指示灯以 1Hz 频率闪烁为红色，亮0.5s后熄灭
温度恢复后，再根据最近一次电池电压恢复 LED 颜色

## 3.6 web监控模块

通过SSE为设备提供监控网页界面和实时数据推送

SSE 数据包括：
- ADC 原始值和换算电压
- 电池电压
- 芯片温度
- GPIO 原始点击计数
- 单击、双击等控制事件
- 相机就绪和保存完成状态
- 相机进程运行日志

浏览器通过“http://<设备IP>:8080”地址访问：

## 3.7 多路摄像头模块

### 3.7.1 扫描设备并存储映射信息

调用 v4l2-ctl --list-devices，生成相机名称到 /dev/videoX 的映射

程序解析设备名称和第一个 `/dev/videoX` 节点，将结果写入 `camera_config.json`

根据名称和 USB 拓扑将设备分类：
main_camera：DECXIN 主相机。
camera_left：USB 左相机。
camera_right：USB 右相机。

### 3.7.2 V4L2 相机采集原理

使用 OpenCV 的 V4L2 后端打开相机，并设置 MJPG、分辨率和帧率：

self.cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

正式采集默认关闭 OpenCV 的 RGB 转换，直接取得相机产生的 MJPEG 字节。程序根据 JPEG 的起止标志 `FF D8` 和 `FF D9` 截取完整图像。JPEG 存储模式下可以直接写入这些字节，省去一次解码和重新编码

每台相机都有一个守护线程持续调用 `cap.read()`，但只保留最新帧

相机不断产生帧 → CameraThread.frame 始终被更新 → 主循环读取当前最新值

### 3.7.3 T265 采集原理

T265Camera开启三个 RealSense 数据流：

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

`get_frame()` 会复制 NumPy 图像后再返回，防止 SDK 回调在写盘期间修改同一块内存

### 3.7.4 多路数据对齐方式

dohc/camera_startup.py 的主循环以 `SYSTEM_FPS=30` 运行，每轮读取各相机当前的最新值，并组成一个 batch：

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

同一个 batch 在写盘时共享一个 `frame_id`，从而实现软件层的数据对齐
该方法是“定期抽取各设备最新值”，而非硬件触发同步


## 3.8 帧队列与后台写盘模块

相机主循环只负责获取最新帧并组成 batch，不直接执行磁盘写入
batch 被放入最大长度为 100 的 frame_queue，由 writer_loop 后台线程消费

队列满时，put_nowait() 会抛出 queue.Full，当前 batch 被丢弃，从而避免磁盘写入过慢时阻塞相机采集或导致内存无限增长

停止录制后，程序先禁止新 batch 入队，再调用 frame_queue.join()
等待已有数据全部写完，最后关闭存储后端并发送 data saved


## 3.9 TCP通信模块

[`dohc/server_client.py`] 和 [`system_monitor/server_client.py`]为内容相同的 Socket 封装

默认协议为：
4 字节大端无符号整数（消息长度） + Pickle 序列化消息

主要事件如下：
| 方向 | 事件 | 含义 |
|---|---|---|
| monitor → camera | `start data collection` | 创建会话并开始录制 |
| monitor → camera | `end data collection` | 停止入队并开始收尾 |
| camera → monitor | `camera ready` | 相机已进入采集主循环 |
| camera → monitor | `data saved` | 队列已清空且存储已关闭 |
| camera → monitor | `{"type": "camera_log"}` | 将相机日志转发到网页 |

监控进程只有收到 `camera ready` 后才接受按键开始/停止操作，避免相机未启动时误触发录制。

## 3.10 数据存储模块

存储模式由 [`dohc/dohc_config.py`] 中的 `RECORDING_STORAGE_MODE` 决定，默认值为 `jpeg`

### 3.10.1 JPEG 模式 

[`JpegDirectoryWriter`](dohc/recording_writer.py) 为一次录制创建：

/mnt/sdcard/<session>/
├─ cam0/          # 主相机
├─ cam1/          # 左 USB 相机
├─ cam2/          # 右 USB 相机
├─ t265_left/     # T265 左鱼眼
├─ t265_right/    # T265 右鱼眼
└─ states.jsonl   # 每帧的时间戳和 Pose

五路图像使用相同文件编号
如果某一路暂时没有图像，Writer 会写一张黑色占位图，而不是跳过编号。Pose 中缺失的数值在 states.jsonl 中写为 JSON null，使五路数据的编号始终保持一致   
V4L2 相机的原始 JPEG 直接写入文件，T265 的灰度 NumPy 图像由 `cv2.imwrite()` 编码
五张图使用线程池并行保存，Pose 则通过独立状态队列增量写入 `states.jsonl`

### 3.10.2 HDF5 模式

[`dohc/utils.py`]将每个 batch 保存成一个 group：

frame_0/
├─ cam0
├─ cam1
├─ cam2
└─ t265/
   ├─ left
   ├─ right
   └─ pose 属性

图像以 JPEG 编码后的 `uint8` 数组保存，Pose 以 JSON 字符串属性保存，一次录制集中在单个文件中

### 3.10.3 Video 模式

[`VideoRecordingWriter`](dohc/recording_writer.py) 输出五个 MP4 文件和一个状态文件：
<session>_cam0.mp4
<session>_cam1.mp4
<session>_cam2.mp4
<session>_t265_left.mp4
<session>_t265_right.mp4
<session>_states.npz

缺失图像使用黑帧补齐，从而保证五个视频帧数相同。状态数据先保存在内存中，在 Writer 关闭时统一生成压缩 NPZ

当前 V4L2 正式采集默认返回原始 JPEG 字节，而 Video Writer 需要解码后的 NumPy 图像
因此直接切换到 `video` 模式前，需要在采集端关闭 `raw_mjpeg`，或在 Video Writer 写入前增加 JPEG 解码

## 3.11 测试模块

### 3.11.1 相机测试

运行示例：
python3 dohc/test_cameras.py --frames 5  采集5帧图像数据
python3 dohc/test_cameras.py --frames 10  采集10帧图像数据
python3 dohc/test_cameras.py --skip-realsense  ：跳过T265相机的初始化与测试，只测试彩色 V4L2 相机

测试程序会为每台相机保存预览图片和 JSON 结果，并生成 `index.html` 供浏览器检查。该测试属于硬件集成测试，不会启动正式的录制控制流程

### 3.11.2 系统监控测试

运行示例：
python3 system_monitor/test_system_monitor.py --skip-led  不刷 LED，只测温度/ADC/GPIO
python3 system_monitor/test_system_monitor.py --led-count 5  测试前 5 个 LED
python3 system_monitor/test_system_monitor.py --watch-gpio 10  监听按键 10 秒

测试内容包括温度节点、ADC、电压换算、WS2812 写入以及 GPIO 边沿事件
