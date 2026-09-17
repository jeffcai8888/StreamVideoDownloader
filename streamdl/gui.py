"""Tkinter 图形界面：下载历史列表 + 新建下载对话框 + 清晰度选择 + 进度条。

启动方式：
    streamdl-gui        # pip install 后
    python main.py --gui
    python -m streamdl.gui
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .bilibili import download_bilibili, get_video_info, is_bilibili, list_qualities
from .downloader import DownloadOptions, StreamDownloader
from .merger import has_ffmpeg, merge
from .parser import MediaPlaylist, parse_m3u8, pick_best_variant

HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".streamdl_history.json")


def _fmt_size(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.2f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    return f"{n / 1024:.0f} KB"


def _default_download_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else os.getcwd()


def load_history() -> list[dict]:
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - 历史文件损坏时从零开始
        return []


def save_history(records: list[dict]) -> None:
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(records[-200:], f, ensure_ascii=False, indent=1)
    except OSError:
        pass


class DownloadDialog(tk.Toplevel):
    """新建下载对话框：输入网址 / SESSDATA / 目录 -> 解析清晰度 -> 开始下载。"""

    def __init__(self, app: "StreamDLApp"):
        super().__init__(app.root)
        self.app = app
        self.title("新建下载")
        self.resizable(False, False)
        self.transient(app.root)
        self.grab_set()

        self._qualities: list[dict] = []   # [{"id", "label"}] 或 m3u8 变体
        self._kind = ""                    # bilibili / m3u8

        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, **pad)

        ttk.Label(frm, text="视频网址:").grid(row=0, column=0, sticky="e", **pad)
        self.url_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.url_var, width=52).grid(row=0, column=1, **pad)

        ttk.Label(frm, text="SESSDATA:").grid(row=1, column=0, sticky="e", **pad)
        self.sess_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.sess_var, width=52, show="*").grid(row=1, column=1, **pad)
        ttk.Label(frm, text="（仅 B 站高清需要，可为空）", foreground="gray").grid(
            row=2, column=1, sticky="w")

        ttk.Label(frm, text="保存目录:").grid(row=3, column=0, sticky="e", **pad)
        dir_frm = ttk.Frame(frm)
        dir_frm.grid(row=3, column=1, sticky="w")
        self.dir_var = tk.StringVar(value=_default_download_dir())
        ttk.Entry(dir_frm, textvariable=self.dir_var, width=42).pack(side="left")
        ttk.Button(dir_frm, text="浏览…", command=self._browse).pack(side="left", padx=4)

        ttk.Label(frm, text="分辨率:").grid(row=4, column=0, sticky="e", **pad)
        self.quality_var = tk.StringVar()
        self.quality_box = ttk.Combobox(frm, textvariable=self.quality_var,
                                        state="readonly", width=49)
        self.quality_box.grid(row=4, column=1, **pad)

        extra_frm = ttk.Frame(frm)
        extra_frm.grid(row=5, column=1, sticky="w")
        self.cover_var = tk.BooleanVar(value=True)
        self.mp3_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(extra_frm, text="同时保存封面",
                        variable=self.cover_var).pack(side="left")
        ttk.Checkbutton(extra_frm, text="同时提取 MP3",
                        variable=self.mp3_var).pack(side="left", padx=8)
        ttk.Label(frm, text="（仅 B 站生效）", foreground="gray").grid(
            row=6, column=1, sticky="w")

        self.status_var = tk.StringVar(value="输入网址后点击「解析清晰度」")
        ttk.Label(frm, textvariable=self.status_var, foreground="gray").grid(
            row=7, column=0, columnspan=2, sticky="w", **pad)

        btn_frm = ttk.Frame(frm)
        btn_frm.grid(row=8, column=0, columnspan=2, pady=8)
        self.parse_btn = ttk.Button(btn_frm, text="解析清晰度", command=self._on_parse)
        self.parse_btn.pack(side="left", padx=6)
        self.dl_btn = ttk.Button(btn_frm, text="开始下载", command=self._on_download,
                                 state="disabled")
        self.dl_btn.pack(side="left", padx=6)
        ttk.Button(btn_frm, text="取消", command=self.destroy).pack(side="left", padx=6)

    def _browse(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.dir_var.get())
        if d:
            self.dir_var.set(d)

    def _make_downloader(self) -> StreamDownloader:
        headers = {}
        sess = self.sess_var.get().strip()
        if sess:
            headers["Cookie"] = f"SESSDATA={sess}"
        return StreamDownloader(DownloadOptions(headers=headers or None))

    # ---------- 解析清晰度 ----------

    def _on_parse(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请先输入视频网址", parent=self)
            return
        self.parse_btn.config(state="disabled")
        self.dl_btn.config(state="disabled")
        self.status_var.set("正在解析…")
        dl = self._make_downloader()  # tk 变量只能在主线程读取
        threading.Thread(target=self._parse_worker, args=(url, dl),
                         daemon=True).start()

    def _parse_worker(self, url: str, dl: StreamDownloader):
        try:
            if is_bilibili(url):
                title, cid, play, view = get_video_info(dl, url)
                qualities = list_qualities(play)
                if not qualities:
                    raise RuntimeError("未获取到可用清晰度")
                self.app.queue.put(("parsed", {
                    "kind": "bilibili", "url": url, "title": title,
                    "qualities": qualities,
                }))
            else:
                text = dl.fetch_text(url)
                result = parse_m3u8(text, url)
                if isinstance(result, MediaPlaylist):
                    qualities = [{"id": None, "label": "默认"}]
                else:
                    qualities = [
                        {"id": v.uri,
                         "label": f"{v.resolution or '未知分辨率'} "
                                  f"(bandwidth={v.bandwidth})"}
                        for v in sorted(result, key=lambda x: x.bandwidth, reverse=True)
                    ]
                self.app.queue.put(("parsed", {
                    "kind": "m3u8", "url": url, "title": "",
                    "qualities": qualities,
                }))
        except Exception as e:  # noqa: BLE001
            self.app.queue.put(("parse_error", str(e)))

    def on_parsed(self, info: dict):
        self._kind = info["kind"]
        self._qualities = info["qualities"]
        labels = [q["label"] for q in self._qualities]
        self.quality_box.config(values=labels)
        self.quality_box.current(0)
        self.dl_btn.config(state="normal")
        self.parse_btn.config(state="normal")
        title = info.get("title") or info["url"]
        self.status_var.set(f"解析成功：{title[:40]}　共 {len(labels)} 个清晰度可选")

    def on_parse_error(self, msg: str):
        self.parse_btn.config(state="normal")
        self.status_var.set(f"解析失败：{msg}")

    # ---------- 开始下载 ----------

    def _on_download(self):
        idx = self.quality_box.current()
        if idx < 0 or not self._qualities:
            messagebox.showwarning("提示", "请先解析并选择分辨率", parent=self)
            return
        out_dir = self.dir_var.get().strip()
        if not os.path.isdir(out_dir):
            messagebox.showwarning("提示", "保存目录不存在", parent=self)
            return
        q = self._qualities[idx]
        self.app.start_download({
            "kind": self._kind,
            "url": self.url_var.get().strip(),
            "sessdata": self.sess_var.get().strip(),
            "dir": out_dir,
            "quality_id": q["id"],
            "quality_label": q["label"],
            "with_cover": self.cover_var.get(),
            "with_mp3": self.mp3_var.get(),
        })
        self.destroy()


class StreamDLApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.queue: queue.Queue = queue.Queue()
        self.history = load_history()
        self._dialog: DownloadDialog | None = None

        root.title("StreamVideoDownloader")
        root.geometry("860x460")

        top = ttk.Frame(root)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Button(top, text="新建下载", command=self.open_dialog).pack(side="left")
        ttk.Button(top, text="打开所在目录",
                   command=self.open_selected_dir).pack(side="left", padx=6)
        ttk.Button(top, text="删除记录", command=self.remove_selected).pack(side="left")

        cols = ("name", "size", "quality", "path", "time")
        heads = {"name": "名称", "size": "大小", "quality": "清晰度",
                 "path": "保存位置", "time": "完成时间"}
        widths = {"name": 240, "size": 90, "quality": 110, "path": 280, "time": 140}
        self.tree = ttk.Treeview(root, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8)
        self.tree.bind("<Double-1>", lambda _e: self.open_selected_dir())

        bottom = ttk.Frame(root)
        bottom.pack(fill="x", padx=8, pady=8)
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bottom, textvariable=self.status_var).pack(anchor="w", pady=2)

        self._reload_tree()
        root.after(100, self._poll_queue)

    # ---------- 历史列表 ----------

    def _reload_tree(self):
        self.tree.delete(*self.tree.get_children())
        for rec in reversed(self.history):
            self.tree.insert("", "end", values=(
                rec.get("name", ""), _fmt_size(rec.get("size", 0)),
                rec.get("quality", ""), rec.get("path", ""), rec.get("time", ""),
            ))

    def _selected_record(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        idx = len(self.history) - 1 - self.tree.index(sel[0])
        return self.history[idx] if 0 <= idx < len(self.history) else None

    def open_selected_dir(self):
        rec = self._selected_record()
        if not rec:
            return
        path = rec.get("path", "")
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("提示", "文件目录不存在（可能已被移动或删除）")
            return
        if sys.platform.startswith("win"):
            os.startfile(folder)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", folder], check=False)
        else:
            subprocess.run(["xdg-open", folder], check=False)

    def remove_selected(self):
        rec = self._selected_record()
        if rec is None:
            return
        self.history.remove(rec)
        save_history(self.history)
        self._reload_tree()

    # ---------- 下载流程 ----------

    def open_dialog(self):
        if self._dialog and self._dialog.winfo_exists():
            self._dialog.lift()
            return
        self._dialog = DownloadDialog(self)

    def start_download(self, params: dict):
        self.progress.config(mode="determinate", value=0)
        self.status_var.set("开始下载…")
        threading.Thread(target=self._download_worker, args=(params,),
                         daemon=True).start()

    def _download_worker(self, p: dict):
        q = self.queue
        try:
            headers = {}
            if p["sessdata"]:
                headers["Cookie"] = f"SESSDATA={p['sessdata']}"
            dl = StreamDownloader(DownloadOptions(headers=headers or None))

            if p["kind"] == "bilibili":
                # 视频流+音频流合计进度
                stream_state: dict[str, tuple[int, int]] = {}

                def on_bytes(label, done, total):
                    stream_state[label] = (done, total)
                    d = sum(x for x, _ in stream_state.values())
                    t = sum(y for _, y in stream_state.values())
                    q.put(("progress", d / t if t else 0,
                           f"{label} {_fmt_size(done)}" + (f" / {_fmt_size(t)}" if t else "")))

                final = download_bilibili(dl, p["url"], None,  # 用视频标题命名
                                          quality_id=p["quality_id"],
                                          on_progress=on_bytes,
                                          with_cover=p["with_cover"],
                                          with_mp3=p["with_mp3"],
                                          out_dir=p["dir"])
                q.put(("done", {
                    "name": os.path.basename(final), "path": final,
                    "size": os.path.getsize(final),
                    "quality": p["quality_label"],
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                }))
            else:
                self._m3u8_download(dl, p, q)
        except Exception as e:  # noqa: BLE001
            q.put(("error", str(e)))

    def _m3u8_download(self, dl: StreamDownloader, p: dict, q: queue.Queue):
        import hashlib
        import shutil

        url = p["url"]
        q.put(("status", "获取播放列表…"))
        media_url = url
        if p["quality_id"]:  # Master Playlist 变体地址
            media_url = p["quality_id"]
        playlist = parse_m3u8(dl.fetch_text(media_url), media_url)
        if not isinstance(playlist, MediaPlaylist):
            raise RuntimeError("解析结果不是分片列表")

        total = len(playlist.segments)
        dl.on_progress = lambda d, t: q.put(
            ("progress", d / t if t else 0, f"分片 {d}/{t}"))
        workdir = os.path.join(".streamdl_tmp",
                               hashlib.md5(url.encode()).hexdigest()[:12])
        dl.download_playlist(playlist, workdir)

        q.put(("progress", 1.0, "合并中…"))
        name = os.path.splitext(os.path.basename(media_url.split("?")[0]))[0] or "video"
        ext = ".mp4" if has_ffmpeg() else ".ts"
        final = merge(playlist, workdir, os.path.join(p["dir"], f"{name}{ext}"))
        shutil.rmtree(workdir, ignore_errors=True)
        q.put(("done", {
            "name": os.path.basename(final), "path": final,
            "size": os.path.getsize(final), "quality": p["quality_label"],
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }))

    # ---------- 队列轮询（主线程更新 UI） ----------

    def _poll_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self.progress.config(mode="determinate",
                                         value=msg[1] * 100)
                    self.status_var.set(f"下载中… {msg[1]*100:.1f}%  {msg[2]}")
                elif kind == "status":
                    self.status_var.set(msg[1])
                elif kind == "done":
                    rec = msg[1]
                    self.progress.config(value=100)
                    self.status_var.set(f"完成：{rec['name']}")
                    self.history.append(rec)
                    save_history(self.history)
                    self._reload_tree()
                    messagebox.showinfo(
                        "下载完成",
                        f"{rec['name']}\n\n大小：{_fmt_size(rec['size'])}"
                        f"\n保存位置：{rec['path']}")
                elif kind == "error":
                    self.progress.config(value=0)
                    self.status_var.set(f"下载失败：{msg[1]}")
                    messagebox.showerror("下载失败", msg[1])
                elif kind == "parsed":
                    if self._dialog and self._dialog.winfo_exists():
                        self._dialog.on_parsed(msg[1])
                elif kind == "parse_error":
                    if self._dialog and self._dialog.winfo_exists():
                        self._dialog.on_parse_error(msg[1])
                        messagebox.showwarning("解析失败", msg[1], parent=self._dialog)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)


def run() -> int:
    root = tk.Tk()
    StreamDLApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run())
