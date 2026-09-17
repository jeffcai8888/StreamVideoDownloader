"""GUI 打包入口（PyInstaller 用）：双击 exe 直接启动图形界面。"""

import sys

from streamdl.gui import run

if __name__ == "__main__":
    sys.exit(run())
