import sys

from PyQt6.QtWidgets import QApplication

from stl_tool import VERSION
from stl_tool.app import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("STLTool")
    app.setApplicationVersion(VERSION)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()