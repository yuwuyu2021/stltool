"""自举环境下的应用入口：无控制台的 pythonw 直接执行本模块来启动 GUI。"""
import sys

from PyQt6.QtWidgets import QApplication

from stl_tool import APP_NAME, VERSION
from stl_tool.app import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()