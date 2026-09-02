from collections import defaultdict, deque, Counter
import time

from PySide6.QtCore import Slot, QPointF, Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QGroupBox, QTabWidget,
    QMessageBox, QFileDialog, QGridLayout, QHeaderView, QLineEdit, QCheckBox,
    QDoubleSpinBox, QSpinBox
)
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtGui import QPainter

from backends.simulated import SimulatedCanBackend
from backends.zlg_backend import ZlgCanBackend
from common.parser import SensorParser
from common.data_logger import CsvCanLogger
from common.sm1810c import (
    SM1810C_QUERY_ID, SM1810C_QUERY_DATA,
    parse_response as parse_sm1810c_response,
)
from .parameter_card import ParameterCard

class MainWindow(QMainWindow):
    MAX_TABLE_ROWS = 1000
    MAX_CHART_POINTS = 300

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CAN Sensor Monitor v4")
        self.resize(1250, 820)

        self.parser = SensorParser()
        self.logger = CsvCanLogger()
        self.backend = None
        self.start_time = time.monotonic()
        self.series = {}
        self.history = defaultdict(lambda: deque(maxlen=self.MAX_CHART_POINTS))
        self.counts = Counter()
        self.total = 0
        self.paused = False

        # SM1810C 实时查询/阈值控制状态
        self.temperature = None
        self._last_temp_status = None
        self._waiting_since = None
        self._query_sent = 0
        self._response_count = 0
        self._timeout_count = 0
        self._box_connection_sent = False
        self._load_box_config()
        self.query_timer = QTimer(self)
        self.query_timer.timeout.connect(self._query_sensor)

        # —— 高频帧节流状态（真实总线可达数千帧/秒） ——
        self._last_stats_update = 0.0
        self._last_y_update = 0.0
        self._filter_cache_text = None
        self._filter_cache = None

        self._build_ui()
        self._create_backend()
        self._rebuild_cards()

    def _build_ui(self):
        self.status = QLabel("状态：未启动")
        self.stats = QLabel("总接收：0 帧 | CAN ID：0 种")

        self.mode = QComboBox()
        self.mode.addItems(["模拟模式", "ZLG USBCAN-II+"])
        self.mode.currentIndexChanged.connect(self._create_backend)
        # 切换数据源时按模式重建参数卡片（模拟=sensor_config，ZLG=温度湿度）
        self.mode.currentIndexChanged.connect(self._rebuild_cards)

        self.channel = QComboBox()
        self.channel.addItems(["CAN0", "CAN1"])

        self.baud = QComboBox()
        self.baud.addItems(
            ["50 kbps", "125 kbps","250 kbps","500 kbps","800 kbps","1 Mbps"]
        )
        # 已通过 CANTest 实测：当前 SM1810C 设备使用 250 kbps。
        # 手册给出的 50 kbps 是出厂默认值，设备参数可能已被改写。
        self.baud.setCurrentText("250 kbps")

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("过滤 CAN ID，如 0x101；留空=全部")

        self.work_mode = QComboBox()
        self.work_mode.addItems(
            ["正常模式", "只读(只听)模式", "自收自发模式"]
        )
        self.work_mode.setCurrentIndex(0)  # 真实传感器查询必须使用正常模式

        self.pause = QCheckBox("暂停界面刷新")
        self.pause.stateChanged.connect(
            lambda state: setattr(
                self, "paused", state == Qt.Checked.value
            )
        )

        self.start_btn = QPushButton("开始")
        self.stop_btn = QPushButton("停止")
        self.save_btn = QPushButton("开始记录 CSV")
        self.clear_btn = QPushButton("清空")
        self.reload_btn = QPushButton("重载参数配置")

        self.start_btn.clicked.connect(self.start_backend)
        self.stop_btn.clicked.connect(self.stop_backend)
        self.save_btn.clicked.connect(self.toggle_logging)
        self.clear_btn.clicked.connect(self.clear_all)
        self.reload_btn.clicked.connect(self.reload_config)

        self.lower_limit = QDoubleSpinBox()
        self.lower_limit.setRange(-100.0, 200.0)
        self.lower_limit.setDecimals(1)
        self.lower_limit.setValue(10.0)
        self.lower_limit.setSuffix(" ℃")
        self.upper_limit = QDoubleSpinBox()
        self.upper_limit.setRange(-100.0, 200.0)
        self.upper_limit.setDecimals(1)
        # 实验要求：温度严格大于 27 ℃时点亮实验箱 DOUT0。
        self.upper_limit.setValue(27.0)
        self.upper_limit.setSuffix(" ℃")
        self.query_interval = QSpinBox()
        self.query_interval.setRange(100, 10000)
        self.query_interval.setValue(500)
        self.query_interval.setSuffix(" ms")
        self.node_id = QSpinBox()
        self.node_id.setRange(1, 247)
        self.node_id.setValue(1)
        self.control_enabled = QCheckBox("启用温度控制")
        self.control_enabled.setChecked(False)
        self.control_state = QLabel("温度状态：等待响应 | 灯控：未启用")
        self.query_timer.setInterval(self.query_interval.value())
        self.query_interval.valueChanged.connect(self.query_timer.setInterval)
        self.control_enabled.stateChanged.connect(self._control_setting_changed)
        self.lower_limit.valueChanged.connect(self._control_setting_changed)
        self.upper_limit.valueChanged.connect(self._control_setting_changed)

        top1 = QHBoxLayout()
        for widget in [
            QLabel("数据源："), self.mode,
            QLabel("通道："), self.channel,
            QLabel("波特率："), self.baud,
        ]:
            top1.addWidget(widget)
        top1.addStretch()
        top1.addWidget(self.start_btn)
        top1.addWidget(self.stop_btn)

        top2 = QHBoxLayout()
        top2.addWidget(QLabel("过滤："))
        top2.addWidget(self.filter)
        top2.addWidget(QLabel("模式："))
        top2.addWidget(self.work_mode)
        top2.addWidget(self.pause)
        top2.addStretch()
        top2.addWidget(self.reload_btn)
        top2.addWidget(self.save_btn)
        top2.addWidget(self.clear_btn)

        control_box = QGroupBox("SM1810C 温度控制（真实 CAN 模式）")
        control_layout = QHBoxLayout(control_box)
        control_layout.addWidget(QLabel("下限："))
        control_layout.addWidget(self.lower_limit)
        control_layout.addWidget(QLabel("上限："))
        control_layout.addWidget(self.upper_limit)
        control_layout.addWidget(QLabel("查询周期："))
        control_layout.addWidget(self.query_interval)
        control_layout.addWidget(QLabel("节点："))
        control_layout.addWidget(self.node_id)
        control_layout.addWidget(self.control_enabled)
        control_layout.addWidget(self.control_state)
        control_layout.addStretch()

        self.params_group = QGroupBox("传感器参数 SM1810C")
        self.params_layout = QGridLayout(self.params_group)
        self.cards = {}

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["序号","PC时间","设备时间/ms","通道","CAN ID","帧类型","DLC","DATA"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setStretchLastSection(True)

        raw_tab = QWidget()
        raw_layout = QVBoxLayout(raw_tab)
        raw_layout.addWidget(self.table)

        self.stats_table = QTableWidget(0, 2)
        self.stats_table.setHorizontalHeaderLabels(["CAN ID", "接收帧数"])
        self.stats_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )

        stats_tab = QWidget()
        stats_layout = QVBoxLayout(stats_tab)
        stats_layout.addWidget(self.stats_table)

        self.chart = QChart()
        self.chart.setTitle("实时参数曲线")
        self.ax = QValueAxis()
        self.ay = QValueAxis()
        self.ax.setTitleText("时间 / s")
        self.ay.setTitleText("参数值")
        self.ax.setRange(0, 30)
        self.ay.setRange(-10, 150)

        self.chart.addAxis(self.ax, Qt.AlignBottom)
        self.chart.addAxis(self.ay, Qt.AlignLeft)

        chart_view = QChartView(self.chart)
        chart_view.setRenderHint(QPainter.Antialiasing)

        chart_tab = QWidget()
        chart_layout = QVBoxLayout(chart_tab)
        chart_layout.addWidget(chart_view)

        tabs = QTabWidget()
        tabs.addTab(raw_tab, "原始 CAN")
        tabs.addTab(chart_tab, "实时曲线")
        tabs.addTab(stats_tab, "报文统计")

        root = QVBoxLayout()
        root.addWidget(self.status)
        root.addWidget(self.stats)
        root.addLayout(top1)
        root.addLayout(top2)
        root.addWidget(control_box)
        root.addWidget(self.params_group)
        root.addWidget(tabs)

        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

    def _rebuild_cards(self):
        while self.params_layout.count():
            widget = self.params_layout.takeAt(0).widget()
            if widget:
                widget.deleteLater()

        self.cards = {}
        seen = set()
        row_index = 0

        # 模拟模式：显示 sensor_config.json 的参数卡片；
        # 真实 ZLG 模式：只显示 SM1810C 温度/湿度卡片。
        if self.mode.currentIndex() == 0:
            for rule in self.parser.rules:
                key = rule.get("key")
                if not key or key in seen:
                    continue
                seen.add(key)

                card = ParameterCard(rule.get("name", key))
                self.cards[key] = card
                self.params_layout.addWidget(
                    card, row_index // 4, row_index % 4
                )
                row_index += 1
        else:
            for key, name in (("temperature", "温度"), ("humidity", "湿度")):
                card = ParameterCard(name)
                self.cards[key] = card
                self.params_layout.addWidget(
                    card, row_index // 4, row_index % 4
                )
                row_index += 1

    def _baud_value(self):
        return {
            "125 kbps": 125000,
            "50 kbps": 50000,
            "250 kbps": 250000,
            "500 kbps": 500000,
            "800 kbps": 800000,
            "1 Mbps": 1000000,
        }[self.baud.currentText()]

    def _create_backend(self):
        if hasattr(self, "query_timer"):
            self.query_timer.stop()
        if self.backend:
            try:
                self.backend.stop()
            except Exception:
                pass
            self.backend.deleteLater()

        if self.mode.currentIndex() == 0:
            self.backend = SimulatedCanBackend(self)
        else:
            self.backend = ZlgCanBackend(
                channel=self.channel.currentIndex(),
                baudrate=self._baud_value(),
                channel_baudrates={
                    int(self.box_config.get(
                        "channel", 1 - self.channel.currentIndex()
                    )): int(self.box_config.get("baudrate", 500000))
                },
                mode=["normal", "listen", "loopback"][
                    self.work_mode.currentIndex()
                ],
                parent=self,
            )

        self.backend.frame_received.connect(self.on_frame)
        self.backend.status_changed.connect(
            lambda text: self.status.setText("状态：" + text)
        )
        self.backend.error_occurred.connect(self.on_error)

    def _load_box_config(self):
        """加载 iCAN-4050 灯控命令配置。"""
        import json
        from pathlib import Path
        self.box_config = {"enabled": False, "commands": {}}
        try:
            path = Path(__file__).resolve().parents[1] / "experiment_box.json"
            self.box_config = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    def _query_sensor(self):
        if self.mode.currentIndex() != 1 or self.work_mode.currentIndex() != 0:
            return
        if not isinstance(self.backend, ZlgCanBackend):
            return
        if self._waiting_since is not None and time.monotonic() - self._waiting_since > 1.0:
            self._timeout_count += 1
            self._waiting_since = None
            self.control_state.setText(
                f"温度状态：等待响应超时（{self._timeout_count} 次） | 灯控：未发送"
            )
        try:
            query = bytes((self.node_id.value(),)) + SM1810C_QUERY_DATA[1:]
            if self.backend.send_frame(
                SM1810C_QUERY_ID, query, channel=self.channel.currentIndex()
            ):
                self._query_sent += 1
                self._waiting_since = time.monotonic()
            else:
                self.control_state.setText(
                    "温度状态：查询发送失败 | "
                    "检查传感器供电、CAN_H/CAN_L、波特率和终端电阻"
                )
        except Exception as exc:
            self.status.setText("状态：发送 SM1810C 查询失败：" + str(exc))

    def _temperature_state(self, value):
        if value < self.lower_limit.value():
            return "low", "低温"
        if value > self.upper_limit.value():
            return "high", "高温"
        return "normal", "正常"

    def _control_setting_changed(self, *_):
        """阈值或启用开关改变后，立即按当前温度重新评估。"""
        if self.temperature is None:
            return
        state, text = self._temperature_state(self.temperature)
        if not self.control_enabled.isChecked():
            self.control_state.setText(f"温度状态：{text} | 灯控：未启用")
            return
        # 阈值或开关改变时，立即把当前状态同步到实验箱；这也保证用户
        # 在已有温度读数后才勾选“启用温度控制”时，灯不会漏发第一条命令。
        self._last_temp_status = state
        if isinstance(self.backend, ZlgCanBackend):
            self._send_box_command(state)

    def _send_box_command(self, state):
        commands = self.box_config.get("commands", {})
        item = commands.get(state, {}) if isinstance(commands, dict) else {}
        data = item.get("data", []) if isinstance(item, dict) else []
        if not self.box_config.get("enabled", False) or not data:
            self.control_state.setText(
                f"温度状态：{dict(low='低温', normal='正常', high='高温').get(state, state)} | "
                "灯控：未配置（experiment_box.json）"
            )
            return
        try:
            box_channel = int(
                self.box_config.get("channel", 1 - self.channel.currentIndex())
            )
            can_id = int(str(self.box_config.get("can_id", "0x200")), 0)
            payload = bytes(int(x, 0) if isinstance(x, str) else int(x) for x in data)

            # iCAN-4050 的写输出命令前，先按实验指导中的示例建立连接：
            # 扩展 ID 0x24F7，DATA=00 00 00。连接超时参数为 00，保持连接，
            # 后续才可稳定执行 0x2120 写输出命令。
            if not self._box_connection_sent:
                connection = self.box_config.get("connection", {})
                if isinstance(connection, dict) and connection.get("enabled", True):
                    connection_id = int(str(connection.get("can_id", "0x24F7")), 0)
                    connection_data = bytes(
                        int(x, 0) if isinstance(x, str) else int(x)
                        for x in connection.get("data", [0x00, 0x00, 0x00])
                    )
                    connected = self.backend.send_frame(
                        connection_id,
                        connection_data,
                        bool(connection.get("extended", True)),
                        channel=box_channel,
                    )
                    if not connected:
                        self.control_state.setText(
                            f"温度状态：{dict(low='低温', normal='正常', high='高温').get(state, state)} | "
                            "iCAN-4050 建立连接发送失败"
                        )
                        return
                    self._box_connection_sent = True

            ok = self.backend.send_frame(
                can_id,
                payload,
                bool(self.box_config.get("extended", False)),
                channel=box_channel,
            )
            self.control_state.setText(
                f"温度状态：{dict(low='低温', normal='正常', high='高温').get(state, state)} | "
                f"灯控：{'已发送' if ok else '发送失败'}"
            )
        except Exception as exc:
            self.control_state.setText("灯控配置错误：" + str(exc))

    def _handle_box_response(self, frame):
        """显示 iCAN-4050 写输出命令的响应，便于区分发送与设备确认。"""
        if frame is None or not getattr(frame, "is_extended", False):
            return
        box_channel = int(
            self.box_config.get("channel", 1 - self.channel.currentIndex())
        )
        if frame.channel != box_channel:
            return

        # SrcMACID=1、DestMACID=0、ACK=1、FuncID=1、SourceID=0x20。
        if frame.can_id == 0x201120:
            if len(frame.data) == 1 and frame.data[0] == 0x00:
                self.control_state.setText(
                    self.control_state.text().replace("灯控：已发送", "灯控：设备已确认")
                )
            elif len(frame.data) >= 2:
                self.control_state.setText(
                    self.control_state.text().replace(
                        "灯控：已发送", f"灯控：设备返回错误码 0x{frame.data[1]:02X}"
                    )
                )

    def _filter_id(self):
        text = self.filter.text().strip()
        if text == self._filter_cache_text:
            return self._filter_cache
        self._filter_cache_text = text
        if not text:
            self._filter_cache = None
            return None
        try:
            self._filter_cache = int(text, 0)
        except ValueError:
            self._filter_cache = "bad"
        return self._filter_cache

    def start_backend(self):
        if self._filter_id() == "bad":
            QMessageBox.warning(
                self, "过滤错误", "请输入例如 0x101 或 257"
            )
            return

        if self.upper_limit.value() <= self.lower_limit.value():
            QMessageBox.warning(self, "阈值错误", "上限必须大于下限")
            return

        # 如果用户修改了通道/波特率，启动前重新创建真实后端
        if self.mode.currentIndex() == 1:
            self._create_backend()

        self.start_time = time.monotonic()
        self.temperature = None
        self._last_temp_status = None
        self._box_connection_sent = False
        self._waiting_since = None
        self.control_state.setText("温度状态：等待响应 | 灯控：未发送")
        self.backend.start()
        if self.mode.currentIndex() == 1 and self.work_mode.currentIndex() == 0:
            self.query_timer.start(self.query_interval.value())

    def stop_backend(self):
        self.query_timer.stop()
        self.backend.stop()

    def on_error(self, text):
        self.status.setText("状态：" + text)
        QMessageBox.warning(self, "提示", text)

    @Slot(object)
    def on_frame(self, frame):
        self.logger.write(frame)

        # iCAN-4050 的响应帧用于更新灯控确认状态；不影响温度解析。
        self._handle_box_response(frame)

        self.total += 1
        self.counts[frame.can_id] += 1
        # 统计标签/表格按 0.3s 节流刷新，避免高频帧下每帧重建表格
        now = time.monotonic()
        if now - self._last_stats_update >= 0.3:
            self.stats.setText(
                f"总接收：{self.total} 帧 | CAN ID：{len(self.counts)} 种"
            )
            self._refresh_stats()
            self._last_stats_update = now

        # SM1810C 响应解析不受“暂停界面刷新”影响，否则暂停时会丢失
        # 阈值判断和灯控动作。
        for sensor_value in parse_sm1810c_response(frame, self.node_id.value()):
            if sensor_value.key == "temperature":
                self.temperature = sensor_value.value
                self._waiting_since = None
                self._response_count += 1
                state, text = self._temperature_state(sensor_value.value)
                if state != self._last_temp_status:
                    self._last_temp_status = state
                    if self.control_enabled.isChecked() and isinstance(self.backend, ZlgCanBackend):
                        self._send_box_command(state)
                    elif not self.control_enabled.isChecked():
                        self.control_state.setText(f"温度状态：{text} | 灯控：未启用")
                    else:
                        self.control_state.setText(f"温度状态：{text} | 灯控：未发送")
                elif not self.control_enabled.isChecked():
                    # 只有在未启用控制时更新“未启用”；控制已发送成功时，
                    # 保留“已发送/发送失败”结果，避免被每次温度轮询覆盖。
                    self.control_state.setText(f"温度状态：{text} | 灯控：未启用")
            if sensor_value.key in self.cards:
                self.cards[sensor_value.key].set_value(sensor_value.value, sensor_value.unit)
            self._append_chart(
                sensor_value.key, sensor_value.name,
                time.monotonic() - self.start_time, sensor_value.value,
            )

        if self.paused:
            return

        filter_id = self._filter_id()
        if filter_id not in (None, "bad") and frame.can_id != filter_id:
            return

        self._append_raw(frame)

        t = time.monotonic() - self.start_time
        for sensor_value in self.parser.parse(frame):
            if sensor_value.key in self.cards:
                self.cards[sensor_value.key].set_value(
                    sensor_value.value, sensor_value.unit
                )
            self._append_chart(
                sensor_value.key,
                sensor_value.name,
                t,
                sensor_value.value,
            )

    def _refresh_stats(self):
        items = sorted(self.counts.items())
        self.stats_table.setRowCount(len(items))

        for row, (can_id, count) in enumerate(items):
            self.stats_table.setItem(
                row, 0, QTableWidgetItem(f"0x{can_id:X}")
            )
            self.stats_table.setItem(
                row, 1, QTableWidgetItem(str(count))
            )

    def _append_raw(self, frame):
        row = self.table.rowCount()
        self.table.insertRow(row)

        if frame.is_error:
            frame_type = "错误帧"
        elif frame.is_remote:
            frame_type = "扩展远程帧" if frame.is_extended else "标准远程帧"
        else:
            frame_type = "扩展数据帧" if frame.is_extended else "标准数据帧"

        values = [
            str(self.total),
            frame.pc_timestamp.strftime("%H:%M:%S.%f")[:-3],
            "" if frame.device_timestamp_us is None else f"{frame.device_timestamp_us/1000:.3f}",
            str(frame.channel),
            f"0x{frame.can_id:X}",
            frame_type,
            str(frame.dlc),
            frame.data_hex,
        ]

        for col, value in enumerate(values):
            self.table.setItem(row, col, QTableWidgetItem(value))

        if self.table.rowCount() > self.MAX_TABLE_ROWS:
            self.table.removeRow(0)

        self.table.scrollToBottom()

    def _append_chart(self, key, name, t, value):
        if key not in self.series:
            series = QLineSeries()
            series.setName(name)
            self.chart.addSeries(series)
            series.attachAxis(self.ax)
            series.attachAxis(self.ay)
            self.series[key] = series

        # 增量追加单个点 + 滑动窗口裁剪，替代整条曲线 replace（O(n)）
        self.history[key].append((t, value))
        series = self.series[key]
        series.append(QPointF(t, value))
        while series.count() > self.MAX_CHART_POINTS:
            series.removePoints(0, 1)

        if t > 30:
            self.ax.setRange(t - 30, t)

        # Y 轴范围按 0.5s 节流重算，避免每帧遍历全部历史点
        now = time.monotonic()
        if now - self._last_y_update >= 0.5:
            ys = [y for hist in self.history.values() for _, y in hist]
            if ys:
                ymin, ymax = min(ys), max(ys)
                margin = max(1.0, (ymax - ymin) * 0.15)
                self.ay.setRange(ymin - margin, ymax + margin)
            self._last_y_update = now

    def reload_config(self):
        try:
            self.parser.reload()
            self._load_box_config()
            self._rebuild_cards()
            self.clear_chart()
            self.status.setText("状态：参数配置已重载")
        except Exception as exc:
            QMessageBox.critical(self, "配置错误", str(exc))

    def clear_chart(self):
        self.history.clear()
        for series in list(self.series.values()):
            self.chart.removeSeries(series)
        self.series.clear()

    def toggle_logging(self):
        if self.logger.is_open:
            self.logger.close()
            self.save_btn.setText("开始记录 CSV")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "保存 CAN 数据", "can_log.csv", "CSV Files (*.csv)"
        )
        if path:
            self.logger.open(path)
            self.save_btn.setText("停止记录 CSV")

    def clear_all(self):
        self.table.setRowCount(0)
        self.stats_table.setRowCount(0)
        self.total = 0
        self.counts.clear()
        self.stats.setText("总接收：0 帧 | CAN ID：0 种")
        self.clear_chart()

        for card in self.cards.values():
            card.value_label.setText("--")
        self.temperature = None
        self._last_temp_status = None
        self._box_connection_sent = False
        self._waiting_since = None
        self.control_state.setText("温度状态：等待响应 | 灯控：未发送")

    def closeEvent(self, event):
        try:
            self.backend.stop()
        except Exception:
            pass
        self.logger.close()
        super().closeEvent(event)
