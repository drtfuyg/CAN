import os
import sys

# zlgcan.py 使用 "./zlgcan.dll" 加载 DLL，因此把当前工作目录固定到项目目录
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("CAN Sensor Monitor")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
