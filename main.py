import sys
import multiprocessing as mp

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from stl_tool import VERSION, app_icon_path
from stl_tool.app import MainWindow


def main():
    mp.freeze_support()
    app = QApplication(sys.argv)
    app.setApplicationName("STLTool")
    app.setApplicationVersion(VERSION)
    icon_path = app_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    win = MainWindow()
    if icon_path:
        win.setWindowIcon(QIcon(icon_path))
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()