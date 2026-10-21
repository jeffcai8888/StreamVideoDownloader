"""GUI 打包入口（PyInstaller 用）：双击 exe 直接启动图形界面。

支持可选参数：
    StreamVideoDownloader.exe          启动图形界面
    StreamVideoDownloader.exe --web    启动网页版（自动打开浏览器）
"""

import sys


def main() -> int:
    if "--web" in sys.argv:
        from streamdl.web import DEFAULT_PORT, run as web_run

        port = DEFAULT_PORT
        if "--port" in sys.argv:
            i = sys.argv.index("--port")
            if i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
        return web_run(port=port)

    from streamdl.gui import run as gui_run
    return gui_run()


if __name__ == "__main__":
    sys.exit(main())
