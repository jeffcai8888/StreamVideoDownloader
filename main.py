#!/usr/bin/env python
"""streamdl 启动入口：python main.py <m3u8_url> [选项]"""

import sys

from streamdl.cli import main

if __name__ == "__main__":
    sys.exit(main())
