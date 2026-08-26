from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

class ParameterCard(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.title_label = QLabel(title)
        self.value_label = QLabel("--")
        self.value_label.setAlignment(Qt.AlignCenter)

        font = self.value_label.font()
        font.setPointSize(20)
        font.setBold(True)
        self.value_label.setFont(font)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value, unit=""):
        self.value_label.setText(
            f"{value:.3f}" + (f" {unit}" if unit else "")
        )
