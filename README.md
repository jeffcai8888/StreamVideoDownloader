# StreamVideoDownloader

[English](#english) | [中文](#中文)

---

## 中文

一个流媒体下载工具，支持 HLS (m3u8) 和 B 站视频下载：多线程分片下载、AES-128 解密、断点续传、自动合并为 MP4。

### 功能特性

- **HLS/m3u8 下载**：解析 Master Playlist（自动选最高码率，也可手动选择清晰度）与 Media Playlist
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

# 推荐安装 ffmpeg（用于合并输出 MP4）
winget install Gyan.FFmpeg        # Windows
brew install ffmpeg               # macOS
sudo apt install ffmpeg           # Debian/Ubuntu
```

### 使用方法

**下载 B 站视频**

```bash
# BV 号 / av 号 / 完整链接均可，自动用视频标题命名
python main.py BV17x411w7KC
python main.py "https://www.bilibili.com/video/BV17x411w7KC/"

# 多 P 视频选择分集
python main.py BV17x411w7KC -p 2

# 高清画质需要登录 Cookie（F12 -> Application -> Cookies -> SESSDATA）
python main.py BV17x411w7KC -H "Cookie: SESSDATA=你的SESSDATA"
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
| `--keep-temp` | 保留临时分片目录 | 不保留 |

### 项目结构

```
streamdl/
├── parser.py       # m3u8 解析（Master/Media Playlist、AES-128 KEY、fMP4 MAP）
├── downloader.py   # 多线程分片下载、AES-128 解密、重试、断点续传
├── bilibili.py     # B 站 API 解析与 DASH 流下载
├── merger.py       # ffmpeg 合并 MP4 / 二进制拼接 TS
└── cli.py          # 命令行入口
```

### 已知限制

- 仅支持 VOD（完整分片列表），不支持直播流持续录制
- 暂不支持 SAMPLE-AES 加密与 DASH/mpd（B 站除外，已内置支持）
- B 站未登录时画质受限（通常 480P 封顶），传入 SESSDATA 可解锁 1080P+

---

## English

A streaming media downloader supporting HLS (m3u8) and Bilibili videos: multi-threaded segment downloading, AES-128 decryption, resume support, and automatic merging to MP4.

### Features

- **HLS/m3u8**: parses Master Playlists (auto-selects highest bandwidth, or pick manually) and Media Playlists
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

# ffmpeg is recommended (for MP4 output)
winget install Gyan.FFmpeg        # Windows
brew install ffmpeg               # macOS
sudo apt install ffmpeg           # Debian/Ubuntu
```

### Usage

**Download a Bilibili video**

```bash
# BV ID / av ID / full URL all work; output is named after the video title
python main.py BV17x411w7KC
python main.py "https://www.bilibili.com/video/BV17x411w7KC/"

# Select a page of a multi-part video
python main.py BV17x411w7KC -p 2

# Higher qualities require a login cookie (F12 -> Application -> Cookies -> SESSDATA)
python main.py BV17x411w7KC -H "Cookie: SESSDATA=your_sessdata"
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
| `--keep-temp` | Keep the temporary segment directory | off |

### Project Structure

```
streamdl/
├── parser.py       # m3u8 parsing (Master/Media Playlist, AES-128 KEY, fMP4 MAP)
├── downloader.py   # Multi-threaded segment download, AES-128 decryption, retry, resume
├── bilibili.py     # Bilibili API resolution and DASH stream download
├── merger.py       # ffmpeg merge to MP4 / binary concat to TS
└── cli.py          # Command-line entry point
```

### Known Limitations

- VOD only (complete segment lists); no live-stream recording
- SAMPLE-AES encryption and generic DASH/mpd are not supported (Bilibili is handled natively)
- Without a Bilibili login cookie, quality is limited (usually 480P max); passing SESSDATA unlocks 1080P+
