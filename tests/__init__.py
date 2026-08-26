"""测试包：确保能导入项目根目录的模块。

运行方式（项目根目录）：
    python -m unittest discover -s tests -v
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
