"""Launch the UNMScope GUI: python -m unmscope.gui"""
import sys

from PySide6.QtWidgets import QApplication

from unmscope.gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
