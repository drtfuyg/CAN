# CAN Sensor Monitor v4.1

本版依据提供的：
- ZLG 2026 接口函数使用手册
- 官方 Python Demo
- 官方 zlgcan.py
- 官方 20260414 x64 zlgcan.dll
- 与之配套的 x64 kerneldlls

进行了校准。

## v4.1 相比 v4 的修改

1. 使用官方 x64 `zlgcan.dll`
2. 项目内加入与 x64 DLL 匹配的 `kerneldlls/`
3. 真实 CAN 后端使用设备返回的 `msg.timestamp`
4. `CanFrame` 同时保存：
   - PC 时间
   - 设备时间戳（微秒）
5. 支持 CAN ID bit29 错误帧标志
6. 原始报文表格新增“设备时间/ms”
7. CSV 同时保存 PC 时间、设备时间和错误帧标志
8. 增加“只听模式”，真实设备首次接入默认开启
9. 保留模拟模式、过滤、统计、曲线、参数配置、CSV

## 运行

在现有的 64 位 myenv 中：

```powershell
cd C:\Users\violet\Desktop\can_sensor_monitor_v4_1
python main.py
```

## 首次真实设备测试建议

1. 先安装 ZLG Windows 驱动
2. 用 ZLG 官方软件确认设备能打开
3. 本程序选择 `ZLG USBCAN-II+`
4. CAN0 / CAN1 按实际接线选择
5. 波特率按传感器/总线协议选择
6. 首次保持“只听模式”勾选
7. 点击“开始”
8. 先观察“原始 CAN”和“报文统计”
9. 确认原始报文正常后，再配置 `sensor_config.json`

## 仍然未知的内容

- 波特率
- CAN ID
- 标准帧/扩展帧使用方式
- 各字节物理含义
- signed/unsigned
- 大小端
- scale
- offset
- 单位
- 是否需要主动向传感器发送命令

这些内容确定后，主要填写 `sensor_config.json`。

## 测试（无设备也能完整验证）

测试基于标准库 `unittest`，不依赖真实硬件与第三方库（GUI 部分除外）。

```powershell
# 项目根目录运行
python -m unittest discover -s tests -v
```

覆盖内容：
- `test_parser.py`：大小端、符号、float32、scale/offset、多信号同 ID、边界与错误配置
- `test_can_utils.py`：ZLG can_id 位标志（bit31/30/29）编解码
- `test_simulation.py`：仿真引擎的周期、抖动、丢帧、远程帧、错误帧、确定性
- `test_data_logger.py`：CSV 表头/数据/远程帧 DLC
- `test_integration.py`：仿真 -> 解析 端到端一致性、无关报文过滤

## 真实总线仿真（模拟模式）

`simulation.py` 提供了贴近真实总线的仿真引擎，`模拟模式` 现在基于它：

- 多周期报文 + 周期抖动（如 100ms 配置报文、20ms 高速无关报文）
- 总线上的"无关报文"（`0x1F0`/`0x1F1`，可验证过滤与统计）
- 信号由真实物理波形（正弦/三角/阶跃/噪声）驱动
- 支持远程帧、错误帧注入、丢帧
- 可在脚本中直接驱动：

```python
from simulation import simulator_from_config
from parser import SensorParser

bus = simulator_from_config("sensor_config.json",
                            error_frame_rate=0.005, seed=1)
frames = bus.run(5.0)               # 模拟 5 秒总线
parser = SensorParser("sensor_config.json")
values = [sv.value for f in frames for sv in parser.parse(f)]
```

GUI 中模拟模式默认开启"真实总线仿真"；错误帧/远程帧注入
可在 `SimulatedCanBackend` 构造时通过 `error_frame_rate` / `remote_frame_ids` 开启。
