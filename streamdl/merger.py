"""分片合并：优先调用 ffmpeg 输出 MP4，否则直接二进制拼接为 TS。"""

import os
import shutil
import subprocess

from .parser import MediaPlaylist


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def merge(playlist: MediaPlaylist, tmp_dir: str, output: str) -> str:
    """合并已下载分片。返回最终输出文件路径。

    - 有 ffmpeg：按顺序 concat 封装为 output 指定格式（默认 .mp4）。
    - 无 ffmpeg：直接二进制拼接，输出 .ts 文件。
    """
    segments = sorted(playlist.segments, key=lambda s: s.index)
    paths = []
    if playlist.map_uri:  # fMP4：初始化段排在最前
        paths.append(os.path.join(tmp_dir, "init.mp4"))
    paths.extend(s.path for s in segments)

    if has_ffmpeg():
        list_file = os.path.join(tmp_dir, "concat.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for p in paths:
                # concat demuxer 以列表文件所在目录解析相对路径，必须写绝对路径
                f.write(f"file '{os.path.abspath(p).replace(os.sep, '/')}'\n")
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", "-bsf:a", "aac_adtstoasc", output,
        ]
        subprocess.run(cmd, check=True)
        return output

    if not output.endswith(".ts"):
        output = os.path.splitext(output)[0] + ".ts"
    with open(output, "wb") as out:
        for p in paths:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out, length=1024 * 1024)
    return output
