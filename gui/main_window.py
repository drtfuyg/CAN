from collections import defaultdict, deque, Counter
import time

from PySide6.QtCore import Slot, QPointF, Qt
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QGroupBox, QTabWidget,
    QMessageBox, QFileDialog, QGridLayout, QHeaderView, QLineEdit, QCheckBox
)
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtGui import QPainter

from backends.simulated import SimulatedCanBackend
from backends.zlg_backend import ZlgCanBackend
from common.parser import SensorParser
from common.data_logger import CsvCanLogger
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

        self.channel = QComboBox()
        self.channel.addItems(["CAN0", "CAN1"])

        self.baud = QComboBox()
        self.baud.addItems(
            ["125 kbps","250 kbps","500 kbps","800 kbps","1 Mbps"]
        )
        self.baud.setCurrentText("500 kbps")

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("过滤 CAN ID，如 0x101；留空=全部")

        self.listen_only = QCheckBox("只听模式")
        self.listen_only.setChecked(True)

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
        top2.addWidget(self.listen_only)
        top2.addWidget(self.pause)
        top2.addStretch()
        top2.addWidget(self.reload_btn)
        top2.addWidget(self.save_btn)
        top2.addWidget(self.clear_btn)

        self.params_group = QGroupBox("传感器参数（sensor_config.json）")
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

    def _baud_value(self):
        return {
            "125 kbps": 125000,
            "250 kbps": 250000,
            "500 kbps": 500000,
            "800 kbps": 800000,
            "1 Mbps": 1000000,
        }[self.baud.currentText()]

    def _create_backend(self):
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
                listen_only=self.listen_only.isChecked(),
                parent=self,
            )

        self.backend.frame_received.connect(self.on_frame)
        self.backend.status_changed.connect(
            lambda text: self.status.setText("状态：" + text)
        )
        self.backend.error_occurred.connect(self.on_error)

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

        # 如果用户修改了通道/波特率，启动前重新创建真实后端
        if self.mode.currentIndex() == 1:
            self._create_backend()

        self.start_time = time.monotonic()
        self.backend.start()

    def stop_backend(self):
        self.backend.stop()

    def on_error(self, text):
        self.status.setText("状态：" + text)
        QMessageBox.warning(self, "提示", text)

    @Slot(object)
    def on_frame(self, frame):
        self.logger.write(frame)

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

    def closeEvent(self, event):
        try:
            self.backend.stop()
        except Exception:
            pass
        self.logger.close()
        super().closeEvent(event)
