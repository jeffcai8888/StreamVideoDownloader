"""并发分片下载器：多线程下载 + AES-128 解密 + 重试 + 断点续传。"""

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import requests

from .parser import MediaPlaylist, Segment

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class DownloadOptions:
    threads: int = 8
    retries: int = 5
    timeout: float = 30.0
    headers: dict[str, str] | None = None
    proxy: str | None = None
    keep_temp: bool = False


def _aes128_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    from Crypto.Cipher import AES

    cipher = AES.new(key, AES.MODE_CBC, iv)
    plain = cipher.decrypt(data)
    pad = plain[-1]
    if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
        plain = plain[:-pad]
    return plain


class StreamDownloader:
    def __init__(self, options: DownloadOptions | None = None):
        self.opt = options or DownloadOptions()
        self.session = requests.Session()
        headers = {"User-Agent": DEFAULT_UA}
        if self.opt.headers:
            headers.update(self.opt.headers)
        self.session.headers.update(headers)
        if self.opt.proxy:
            self.session.proxies = {"http": self.opt.proxy, "https": self.opt.proxy}
        self._key_cache: dict[str, bytes] = {}
        self._key_lock = threading.Lock()
        self._progress_lock = threading.Lock()
        self._done = 0
        self._total = 0
        self._failed: list[Segment] = []

    # ---------- 网络 ----------

    def fetch_text(self, url: str) -> str:
        resp = self.session.get(url, timeout=self.opt.timeout)
        resp.raise_for_status()
        return resp.text

    def _fetch_bytes(self, url: str, byte_range: tuple[int, int] | None = None) -> bytes:
        headers = {}
        if byte_range:
            length, offset = byte_range
            headers["Range"] = f"bytes={offset}-{offset + length - 1}"
        last_err: Exception | None = None
        for attempt in range(self.opt.retries):
            try:
                resp = self.session.get(url, headers=headers, timeout=self.opt.timeout)
                resp.raise_for_status()
                return resp.content
            except Exception as e:  # noqa: BLE001 - 重试任意网络错误
                last_err = e
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"下载失败（重试 {self.opt.retries} 次）: {url} -> {last_err}")

    def _get_key(self, uri: str) -> bytes:
        with self._key_lock:
            if uri not in self._key_cache:
                self._key_cache[uri] = self._fetch_bytes(uri)
        key = self._key_cache[uri]
        if len(key) != 16:
            raise RuntimeError(f"AES-128 密钥长度应为 16 字节，实际 {len(key)}: {uri}")
        return key

    # ---------- 分片 ----------

    def _download_segment(self, seg: Segment, tmp_dir: str) -> Segment:
        out_path = os.path.join(tmp_dir, f"{seg.index:06d}.ts")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            seg.path = out_path  # 断点续传：跳过已完成分片
            self._tick(seg)
            return seg

        data = self._fetch_bytes(seg.uri, seg.byte_range)

        if seg.key and seg.key.method.upper() == "AES-128":
            key = self._get_key(seg.key.uri)
            iv = seg.key.iv if seg.key.iv else seg.index.to_bytes(16, "big")
            data = _aes128_decrypt(data, key, iv)
        elif seg.key and seg.key.method.upper() != "NONE":
            raise RuntimeError(f"不支持的加密方式: {seg.key.method}")

        with open(out_path, "wb") as f:
            f.write(data)
        seg.path = out_path
        self._tick(seg)
        return seg

    def _tick(self, seg: Segment) -> None:
        with self._progress_lock:
            self._done += 1
            pct = self._done / self._total * 100 if self._total else 0
            sys.stderr.write(
                f"\r下载进度: {self._done}/{self._total} ({pct:.1f}%) "
                f"分片 {os.path.basename(seg.uri)[:40]:<40}"
            )
            sys.stderr.flush()

    # ---------- 入口 ----------

    def download_playlist(self, playlist: MediaPlaylist, tmp_dir: str) -> MediaPlaylist:
        """下载整个 Media Playlist 到 tmp_dir，返回填充了本地路径的 playlist。"""
        if not playlist.segments:
            raise RuntimeError("播放列表中没有分片")

        os.makedirs(tmp_dir, exist_ok=True)
        self._total = len(playlist.segments)
        self._done = 0
        self._failed = []

        # fMP4 初始化段
        if playlist.map_uri:
            init_path = os.path.join(tmp_dir, "init.mp4")
            if not os.path.exists(init_path):
                with open(init_path, "wb") as f:
                    f.write(self._fetch_bytes(playlist.map_uri))

        with ThreadPoolExecutor(max_workers=self.opt.threads) as pool:
            futures = {
                pool.submit(self._download_segment, seg, tmp_dir): seg
                for seg in playlist.segments
            }
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:  # noqa: BLE001 - 收集所有失败分片
                    self._failed.append(futures[fut])
                    sys.stderr.write(f"\n[失败] {futures[fut].uri}: {e}\n")

        sys.stderr.write("\n")
        if self._failed:
            raise RuntimeError(f"{len(self._failed)} 个分片下载失败，可重试（支持断点续传）")
        return playlist

    def download_live(self, url: str, tmp_dir: str) -> MediaPlaylist:
        """录制直播流：轮询 m3u8，增量下载新分片，直到出现 EXT-X-ENDLIST 或用户中断。

        按 media sequence 去重（滑动窗口会丢弃旧分片），顺序下载保证时效性。
        返回已下载分片组成的 playlist，供合并使用。
        """
        from .parser import parse_m3u8

        os.makedirs(tmp_dir, exist_ok=True)
        seen: set[int] = set()
        collected: list[Segment] = []
        self._failed = []

        print("直播录制中，按 Ctrl+C 停止并合并已下载内容...")
        interrupted = False
        try:
            while True:
                try:
                    playlist = parse_m3u8(self.fetch_text(url), url)
                except Exception as e:  # noqa: BLE001 - 单次拉取失败不终止录制
                    sys.stderr.write(f"\n[警告] 拉取播放列表失败，稍后重试: {e}\n")
                    time.sleep(2)
                    continue

                if not isinstance(playlist, MediaPlaylist):
                    raise RuntimeError("直播地址不应为 Master Playlist，请先解析变体")

                new_segments = []
                for i, seg in enumerate(playlist.segments):
                    seq = playlist.media_sequence + i
                    if seq not in seen:
                        seen.add(seq)
                        seg.index = seq
                        new_segments.append(seg)

                self._total = len(seen)
                self._done = self._total - len(new_segments)
                for seg in new_segments:  # 直播按序下载，优先时效
                    try:
                        self._download_segment(seg, tmp_dir)
                        collected.append(seg)
                    except Exception as e:  # noqa: BLE001
                        self._failed.append(seg)
                        sys.stderr.write(f"\n[失败] {seg.uri}: {e}\n")

                if playlist.endlist:
                    break
                time.sleep(max(playlist.target_duration / 2, 1.0))
        except KeyboardInterrupt:
            interrupted = True
            sys.stderr.write("\n已停止录制，开始合并...\n")

        sys.stderr.write("\n")
        if not collected:
            raise RuntimeError("未下载到任何分片")
        result = MediaPlaylist()
        result.segments = sorted(collected, key=lambda s: s.index)
        result.endlist = not interrupted
        return result
