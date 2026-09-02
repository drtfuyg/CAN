import os
import sys
from pathlib import Path

def _configure_qt_plugins():
    """让从 Anaconda/venv 启动时也能找到 PySide6 的 Windows 插件。"""
    # PySide6 通常安装在当前 Python 的 site-packages 下；也允许用
    # PYSIDE6_DIR 覆盖，便于不同环境或便携式安装。
    candidates = []
    override = os.environ.get("PYSIDE6_DIR")
    if override:
        candidates.append(Path(override))
    candidates.append(Path(sys.prefix) / "Lib" / "site-packages" / "PySide6")
    for pyside_dir in candidates:
        plugins = pyside_dir / "plugins"
        platforms = plugins / "platforms"
        if not (platforms / "qwindows.dll").exists():
            continue
        if not os.environ.get("QT_PLUGIN_PATH"):
            os.environ["QT_PLUGIN_PATH"] = str(plugins)
        if not os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH"):
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platforms)
        # Qt 依赖的 DLL（Qt6Core、平台插件依赖等）也应在搜索路径中。
        old_path = os.environ.get("PATH", "")
        if str(pyside_dir) not in old_path.split(os.pathsep):
            os.environ["PATH"] = str(pyside_dir) + os.pathsep + old_path
        try:
            os.add_dll_directory(str(pyside_dir))
        except (AttributeError, OSError):
            pass
        break


_configure_qt_plugins()

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
