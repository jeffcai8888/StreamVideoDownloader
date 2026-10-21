"""网页版：本地 HTTP 服务 + 纯 HTML 前端（无任何前端框架）。

浏览器无法直接跨域抓取各视频站点（CORS 限制），因此需要这个本地后端
代为下载；前端是一个纯 HTML 页面。默认监听 127.0.0.1:8670。

启动：
    streamdl-web            # pip install 后
    python main.py --web
"""

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .bili_login import load_sessdata
from .bilibili import download_bilibili, get_video_info, is_bilibili, list_qualities
from .downloader import DownloadOptions, StreamDownloader
from .history import load_history, save_history
from .merger import has_ffmpeg, merge
from .parser import MediaPlaylist, parse_m3u8

DEFAULT_PORT = 8670
TASKS: dict[str, dict] = {}
TASKS_LOCK = threading.Lock()


def _default_download_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else os.getcwd()


def _set_task(tid: str, **fields) -> None:
    with TASKS_LOCK:
        TASKS.setdefault(tid, {}).update(fields)


def parse_video(url: str, sessdata: str = "", cookiefile: str | None = None) -> dict:
    """解析视频信息，返回 {kind, title, qualities:[{id,label}]}。与 GUI 逻辑一致。"""
    headers = {}
    if sessdata:
        headers["Cookie"] = f"SESSDATA={sessdata}"
    dl = StreamDownloader(DownloadOptions(headers=headers or None))

    if is_bilibili(url):
        title, _cid, play, _view = get_video_info(dl, url)
        qualities = list_qualities(play)
        if not qualities:
            raise RuntimeError("未获取到可用清晰度")
        return {"kind": "bilibili", "title": title, "qualities": qualities}

    try:
        result = parse_m3u8(dl.fetch_text(url), url)
    except Exception:  # noqa: BLE001 - 非 m3u8，交给 yt-dlp
        from .ytdlp_bridge import list_qualities as yt_list_qualities
        title, qualities = yt_list_qualities(url, cookiefile=cookiefile)
        return {"kind": "ytdlp", "title": title, "qualities": qualities}

    if isinstance(result, MediaPlaylist):
        qualities = [{"id": None, "label": "默认"}]
    else:
        qualities = [
            {"id": v.uri,
             "label": f"{v.resolution or '未知分辨率'} (bandwidth={v.bandwidth})"}
            for v in sorted(result, key=lambda x: x.bandwidth, reverse=True)
        ]
    return {"kind": "m3u8", "title": "", "qualities": qualities}


def _run_download(tid: str, p: dict) -> None:
    def progress(frac: float, text: str):
        _set_task(tid, status="downloading", percent=round(frac * 100, 1), text=text)

    try:
        headers = {}
        sessdata = p.get("sessdata") or load_sessdata()
        if sessdata:
            headers["Cookie"] = f"SESSDATA={sessdata}"
        dl = StreamDownloader(DownloadOptions(headers=headers or None))
        out_dir = p.get("dir") or _default_download_dir()
        os.makedirs(out_dir, exist_ok=True)

        if p["kind"] == "bilibili":
            stream_state: dict[str, tuple[int, int]] = {}

            def on_bytes(label, done, total):
                stream_state[label] = (done, total)
                d = sum(x for x, _ in stream_state.values())
                t = sum(y for _, y in stream_state.values())
                progress(d / t if t else 0, f"{label} {d // 1048576} MB")

            final = download_bilibili(dl, p["url"], None,
                                      quality_id=p.get("quality_id"),
                                      on_progress=on_bytes,
                                      with_cover=p.get("with_cover", False),
                                      with_mp3=p.get("with_mp3", False),
                                      out_dir=out_dir)
        elif p["kind"] == "ytdlp":
            from .ytdlp_bridge import download as yt_download

            def on_bytes2(label, done, total):
                progress(done / total if total else 0,
                         f"{label} {done // 1048576} MB")

            final = yt_download(p["url"], out_dir, format_id=p.get("quality_id"),
                                cookiefile=p.get("cookiefile") or None,
                                on_progress=on_bytes2)
        else:  # m3u8
            media_url = p.get("quality_id") or p["url"]
            playlist = parse_m3u8(dl.fetch_text(media_url), media_url)
            if not isinstance(playlist, MediaPlaylist):
                raise RuntimeError("解析结果不是分片列表")
            dl.on_progress = lambda d, t: progress(d / t if t else 0,
                                                   f"分片 {d}/{t}")
            workdir = os.path.join(
                ".streamdl_tmp", hashlib.md5(p["url"].encode()).hexdigest()[:12])
            dl.download_playlist(playlist, workdir)
            progress(1.0, "合并中…")
            name = os.path.splitext(
                os.path.basename(media_url.split("?")[0]))[0] or "video"
            ext = ".mp4" if has_ffmpeg() else ".ts"
            final = merge(playlist, workdir, os.path.join(out_dir, f"{name}{ext}"))
            shutil.rmtree(workdir, ignore_errors=True)

        record = {
            "name": os.path.basename(final), "path": final,
            "size": os.path.getsize(final), "quality": p.get("quality_label", ""),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        history = load_history()
        history.append(record)
        save_history(history)
        _set_task(tid, status="done", percent=100, record=record)
    except Exception as e:  # noqa: BLE001
        _set_task(tid, status="error", error=str(e))


INDEX_HTML = os.path.join(os.path.dirname(__file__), "web", "index.html")


class Handler(BaseHTTPRequestHandler):
    server_version = "StreamVideoDownloader/0.3"

    def log_message(self, *args):  # 静默请求日志
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"请求体不是合法 JSON: {e}") from e

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(INDEX_HTML, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/history":
            self._json({"records": load_history(),
                        "default_dir": _default_download_dir(),
                        "sessdata_saved": bool(load_sessdata())})
        elif self.path.startswith("/api/task/"):
            tid = self.path.rsplit("/", 1)[-1]
            with TASKS_LOCK:
                task = TASKS.get(tid)
            self._json(task or {"status": "unknown"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            data = self._body()
        except ValueError as e:
            self._json({"ok": False, "error": str(e)}, 400)
            return
        if self.path == "/api/parse":
            try:
                result = parse_video(data.get("url", "").strip(),
                                     data.get("sessdata", "").strip(),
                                     data.get("cookiefile") or None)
                self._json({"ok": True, **result})
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)})
        elif self.path == "/api/download":
            if not data.get("url"):
                self._json({"ok": False, "error": "缺少 url"}, 400)
                return
            tid = uuid.uuid4().hex[:12]
            _set_task(tid, status="downloading", percent=0, text="开始下载…")
            threading.Thread(target=_run_download, args=(tid, data),
                             daemon=True).start()
            self._json({"ok": True, "task_id": tid})
        else:
            self._json({"error": "not found"}, 404)


def run(port: int = DEFAULT_PORT, open_browser: bool = True) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"网页版已启动: {url}（按 Ctrl+C 停止）")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
