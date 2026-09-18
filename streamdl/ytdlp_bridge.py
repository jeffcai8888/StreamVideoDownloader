"""yt-dlp 集成：YouTube、抖音等站点的清晰度解析与下载。

对既不是 B 站也不是 m3u8 的链接，交给 yt-dlp 处理。
GUI/CLI 通过 list_qualities() 提供分辨率选择，通过 download() 下载。
"""

import os

import yt_dlp

from .merger import find_ffmpeg


def _base_opts(proxy: str | None = None, cookiefile: str | None = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "socket_timeout": 30,
    }
    if proxy:
        opts["proxy"] = proxy
    if cookiefile:
        opts["cookiefile"] = cookiefile
    return opts


# 部分站点（如抖音）需要浏览器里的新鲜 Cookie（无需登录），按常见浏览器依次尝试
_BROWSERS = ("edge", "chrome", "firefox", "brave")


def _needs_cookies(err: Exception) -> bool:
    return "cookie" in str(err).lower()


def _extract_info(url: str, download: bool, proxy: str | None,
                  extra: dict | None = None,
                  cookiefile: str | None = None) -> dict:
    """带 Cookie 回退的 extract_info：优先 cookies.txt 文件，其次浏览器 Cookie。"""
    base = _base_opts(proxy, cookiefile)
    if extra:
        base.update(extra)
    try:
        with yt_dlp.YoutubeDL(base) as ydl:
            return ydl.extract_info(url, download=download)
    except Exception as e:  # noqa: BLE001
        if cookiefile or not _needs_cookies(e):
            raise
    last_err: Exception | None = None
    for browser in _BROWSERS:
        try:
            opts = dict(base)
            opts["cookiesfrombrowser"] = (browser,)
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=download)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(
        "该站点需要 Cookie 验证。解决方法（任选其一）：\n"
        "1. 先在浏览器中打开一次该网站（抖音/YouTube），保持浏览器登录状态后重试；\n"
        "2. 用浏览器扩展（如 Get cookies.txt LOCALLY）导出 cookies.txt，"
        "然后在界面中选择该文件（CLI 用 --cookies 参数）。\n"
        f"原始错误: {last_err}"
    )


def list_qualities(url: str, proxy: str | None = None,
                   cookiefile: str | None = None) -> tuple[str, list[dict]]:
    """解析视频信息，返回 (标题, 清晰度选项列表)。

    每个选项 {"id": yt-dlp format selector, "label": "1080P"}，按清晰度从高到低。
    """
    info = _extract_info(url, download=False, proxy=proxy, cookiefile=cookiefile)

    if "entries" in info:  # 播放列表/合集取第一个
        info = next(e for e in info["entries"] if e)

    title = info.get("title") or "video"
    heights: dict[int, dict] = {}
    for f in info.get("formats") or []:
        if f.get("vcodec") in (None, "none"):
            continue
        h = f.get("height")
        if not h:
            continue
        cur = heights.get(h)
        if cur is None or (f.get("tbr") or 0) > (cur.get("tbr") or 0):
            heights[h] = f

    qualities = [
        {"id": f"bv*[height<={h}]+ba/b[height<={h}]/b", "label": f"{h}P"}
        for h in sorted(heights, reverse=True)
    ]
    if not qualities:
        qualities = [{"id": "bv*+ba/b", "label": "默认（最高质量）"}]
    return title, qualities


def download(url: str, out_dir: str, format_id: str | None = None,
             proxy: str | None = None, cookiefile: str | None = None,
             on_progress=None) -> str:
    """下载视频到 out_dir，返回最终文件路径。

    on_progress(label, done_bytes, total_bytes)，total 可能为 0（未知）。
    """
    os.makedirs(out_dir, exist_ok=True)

    def _hook(d):
        if not on_progress:
            return
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            on_progress("视频", d.get("downloaded_bytes", 0), total)
        elif d["status"] == "finished":
            on_progress("合并处理", 1, 1)

    extra = {
        "format": format_id or "bv*+ba/b",
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "progress_hooks": [_hook],
    }
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        extra["ffmpeg_location"] = os.path.dirname(ffmpeg)

    info = _extract_info(url, download=True, proxy=proxy, extra=extra,
                         cookiefile=cookiefile)
    if "entries" in info:
        info = next(e for e in info["entries"] if e)

    with yt_dlp.YoutubeDL({"quiet": True, "outtmpl": extra["outtmpl"]}) as ydl:
        path = ydl.prepare_filename(info)

    # 合并后扩展名可能变为 .mp4
    if os.path.exists(path):
        return path
    base = os.path.splitext(path)[0]
    for ext in (".mp4", ".mkv", ".webm", ".flv"):
        if os.path.exists(base + ext):
            return base + ext
    return path
