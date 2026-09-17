# StreamVideoDownloader

[English](#english) | [中文](#中文)

---

## 中文

一个流媒体下载工具，支持 HLS (m3u8) 和 B 站视频下载：多线程分片下载、AES-128 解密、断点续传、自动合并为 MP4。

### 功能特性

- **HLS/m3u8 下载**：解析 Master Playlist（自动选最高码率，也可手动选择清晰度）与 Media Playlist
- **直播录制**：`--live` 模式持续轮询 m3u8 增量下载，直到直播结束或 Ctrl+C 停止后自动合并
- **B 站视频下载**：直接传入 BV 号 / av 号 / 视频链接，自动获取 DASH 音视频流并无损合并
- **AES-128 解密**：自动下载密钥并解密加密分片（含显式 IV 与默认 IV）
- **多线程并发**：默认 8 线程下载分片，失败自动重试（指数退避）
- **断点续传**：中断后重新运行命令即可从上次进度继续
- **自定义请求**：支持自定义请求头（Referer、Cookie 等）与 HTTP 代理
- **自动合并**：检测到 ffmpeg 时输出 MP4，否则拼接为 TS

### 安装

```bash
# 需要 Python 3.10+
pip install -r requirements.txt
# 或安装为命令行工具（之后可直接使用 streamdl 命令）
pip install .

# 推荐安装 ffmpeg（用于合并输出 MP4）
winget install Gyan.FFmpeg        # Windows
brew install ffmpeg               # macOS
sudo apt install ffmpeg           # Debian/Ubuntu
```

### 使用方法

**图形界面（GUI）**

```bash
streamdl-gui          # pip install . 之后
python main.py --gui  # 或直接运行
```

也可以打包成单文件 exe（双击启动，无需安装 Python）：

```bash
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name StreamVideoDownloader gui_app.py
# 产物在 dist/StreamVideoDownloader.exe
```

> 注意：exe 不包含 ffmpeg，合并输出 MP4 仍需单独安装 ffmpeg。

主界面展示已下载内容（名称、大小、清晰度、保存位置、完成时间），双击记录可打开所在目录。点击「新建下载」弹出对话框：输入视频网址、SESSDATA（仅 B 站高清需要）、选择保存目录，点击「解析清晰度」后选择分辨率，再点「开始下载」，主界面底部显示实时进度条。

**下载 B 站视频**

```bash
# BV 号 / av 号 / 完整链接均可，自动用视频标题命名
python main.py BV17x411w7KC
python main.py "https://www.bilibili.com/video/BV17x411w7KC/"

# 多 P 视频选择分集
python main.py BV17x411w7KC -p 2

# 高清画质需要登录 Cookie（F12 -> Application -> Cookies -> SESSDATA）
python main.py BV17x411w7KC -H "Cookie: SESSDATA=你的SESSDATA"

# 同时下载封面图片和 MP3 音频
python main.py BV17x411w7KC --with-cover --with-mp3
```

**下载 m3u8 / HLS 流**

```bash
python main.py "https://example.com/index.m3u8" -o video.mp4

# 自定义请求头（防盗链场景）与代理
python main.py "https://example.com/index.m3u8" \
  -H "Referer: https://example.com" \
  --proxy http://127.0.0.1:7890

# Master Playlist 手动选择清晰度
python main.py "https://example.com/master.m3u8" --select

# 直播录制（Ctrl+C 停止并自动合并）
python main.py "https://example.com/live/index.m3u8" --live -o live.mp4
```

### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `url` | m3u8 地址，或 B 站 BV 号 / av 号 / 链接 | 必填 |
| `-o, --output` | 输出文件名（B 站视频默认用标题命名） | `output.mp4` |
| `-p, --page` | B 站分 P 序号 | `1` |
| `-t, --threads` | 并发下载线程数 | `8` |
| `--retries` | 单分片重试次数 | `5` |
| `--timeout` | 网络超时（秒） | `30` |
| `-H, --header` | 自定义请求头，可多次指定 | 无 |
| `--proxy` | HTTP 代理 | 无 |
| `--select` | Master Playlist 手动选清晰度 | 自动最高码率 |
| `--live` | 直播录制模式（轮询直到 ENDLIST 或 Ctrl+C） | 关闭 |
| `--with-cover` | B 站视频同时下载封面图片 | 关闭 |
| `--with-mp3` | B 站视频同时提取 MP3 音频 | 关闭 |
| `--keep-temp` | 保留临时分片目录 | 不保留 |

### 项目结构

```
streamdl/
├── parser.py       # m3u8 解析（Master/Media Playlist、AES-128 KEY、fMP4 MAP）
├── downloader.py   # 多线程分片下载、AES-128 解密、重试、断点续传、直播录制
├── bilibili.py     # B 站 API 解析与 DASH 流下载
├── merger.py       # ffmpeg 合并 MP4 / 二进制拼接 TS
├── gui.py          # Tkinter 图形界面
└── cli.py          # 命令行入口
```

### 已知限制

- 暂不支持 SAMPLE-AES 加密与通用 DASH/mpd（B 站除外，已内置支持）
- B 站未登录时画质受限（通常 480P 封顶），传入 SESSDATA 可解锁 1080P+

---

## English

A streaming media downloader supporting HLS (m3u8) and Bilibili videos: multi-threaded segment downloading, AES-128 decryption, resume support, and automatic merging to MP4.

### Features

- **HLS/m3u8**: parses Master Playlists (auto-selects highest bandwidth, or pick manually) and Media Playlists
- **Live recording**: `--live` mode polls the m3u8 and downloads new segments until the stream ends or you press Ctrl+C, then merges automatically
- **Bilibili**: pass a BV ID / av ID / video URL directly; fetches DASH audio+video streams and merges them losslessly
- **AES-128 decryption**: automatically fetches keys and decrypts encrypted segments (explicit or default IV)
- **Multi-threading**: 8 concurrent download threads by default, with automatic retries (exponential backoff)
- **Resume**: interrupted downloads continue from where they left off when re-run
- **Custom requests**: custom headers (Referer, Cookie, etc.) and HTTP proxy support
- **Auto-merge**: outputs MP4 when ffmpeg is available, otherwise concatenates to TS

### Installation

```bash
# Requires Python 3.10+
pip install -r requirements.txt
# Or install as a CLI tool (the `streamdl` command becomes available)
pip install .

# ffmpeg is recommended (for MP4 output)
winget install Gyan.FFmpeg        # Windows
brew install ffmpeg               # macOS
sudo apt install ffmpeg           # Debian/Ubuntu
```

### Usage

**Graphical interface (GUI)**

```bash
streamdl-gui          # after pip install .
python main.py --gui  # or run directly
```

You can also build a standalone exe (double-click to launch, no Python required):

```bash
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name StreamVideoDownloader gui_app.py
# output: dist/StreamVideoDownloader.exe
```

> Note: ffmpeg is not bundled in the exe — install it separately for MP4 merging.

The main window lists downloaded items (name, size, quality, location, finish time); double-click a record to open its folder. Click "新建下载" (New Download) to open the dialog: enter the video URL, SESSDATA (only needed for high-quality Bilibili downloads), and pick a save directory. Click "解析清晰度" (Parse Qualities), choose a resolution, then "开始下载" (Start Download) — a live progress bar shows at the bottom of the main window.

**Download a Bilibili video**

```bash
# BV ID / av ID / full URL all work; output is named after the video title
python main.py BV17x411w7KC
python main.py "https://www.bilibili.com/video/BV17x411w7KC/"

# Select a page of a multi-part video
python main.py BV17x411w7KC -p 2

# Higher qualities require a login cookie (F12 -> Application -> Cookies -> SESSDATA)
python main.py BV17x411w7KC -H "Cookie: SESSDATA=your_sessdata"

# Also save the cover image and extract an MP3 audio track
python main.py BV17x411w7KC --with-cover --with-mp3
```

**Download an m3u8 / HLS stream**

```bash
python main.py "https://example.com/index.m3u8" -o video.mp4

# Custom headers (anti-hotlinking) and proxy
python main.py "https://example.com/index.m3u8" \
  -H "Referer: https://example.com" \
  --proxy http://127.0.0.1:7890

# Manually pick a quality from a Master Playlist
python main.py "https://example.com/master.m3u8" --select

# Record a live stream (Ctrl+C to stop and merge)
python main.py "https://example.com/live/index.m3u8" --live -o live.mp4
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `url` | m3u8 URL, or Bilibili BV ID / av ID / URL | required |
| `-o, --output` | Output filename (Bilibili videos default to the video title) | `output.mp4` |
| `-p, --page` | Bilibili page number for multi-part videos | `1` |
| `-t, --threads` | Concurrent download threads | `8` |
| `--retries` | Retries per segment | `5` |
| `--timeout` | Network timeout in seconds | `30` |
| `-H, --header` | Custom header, repeatable | none |
| `--proxy` | HTTP proxy | none |
| `--select` | Manually pick quality from a Master Playlist | highest bandwidth |
| `--live` | Live recording mode (polls until ENDLIST or Ctrl+C) | off |
| `--with-cover` | Also download the Bilibili cover image | off |
| `--with-mp3` | Also extract an MP3 audio track (Bilibili) | off |
| `--keep-temp` | Keep the temporary segment directory | off |

### Project Structure

```
streamdl/
├── parser.py       # m3u8 parsing (Master/Media Playlist, AES-128 KEY, fMP4 MAP)
├── downloader.py   # Multi-threaded segment download, AES-128 decryption, retry, resume, live recording
├── bilibili.py     # Bilibili API resolution and DASH stream download
├── merger.py       # ffmpeg merge to MP4 / binary concat to TS
├── gui.py          # Tkinter graphical interface
└── cli.py          # Command-line entry point
```

### Known Limitations

- SAMPLE-AES encryption and generic DASH/mpd are not supported (Bilibili is handled natively)
- Without a Bilibili login cookie, quality is limited (usually 480P max); passing SESSDATA unlocks 1080P+
