"""分片合并：优先调用 ffmpeg 输出 MP4，否则直接二进制拼接为 TS。"""

import os
import shutil
import subprocess
import sys

from .parser import MediaPlaylist

_ffmpeg_path: str | None = None


def find_ffmpeg() -> str | None:
    """查找 ffmpeg 可执行文件，结果缓存。

    依次尝试：PATH -> exe 同目录（便携分发）-> winget 安装目录 -> C:\\ffmpeg。
    GUI/exe 双击启动时可能继承旧 PATH（winget 装完未重启资源管理器），
    仅靠 shutil.which 会漏判，因此需要兜底搜索。
    """
    global _ffmpeg_path
    if _ffmpeg_path is not None:
        return _ffmpeg_path or None

    path = shutil.which("ffmpeg")

    if not path and getattr(sys, "frozen", False):
        # PyInstaller 打包后：查找 exe 旁边的 ffmpeg.exe 或 bin/ffmpeg.exe
        exe_dir = os.path.dirname(sys.executable)
        for rel in ("ffmpeg.exe", os.path.join("bin", "ffmpeg.exe")):
            cand = os.path.join(exe_dir, rel)
            if os.path.isfile(cand):
                path = cand
                break

    if not path:
        candidates = []
        local = os.environ.get("LOCALAPPDATA", "")
        winget = os.path.join(local, "Microsoft", "WinGet", "Packages") if local else ""
        if os.path.isdir(winget):
            for pkg in os.listdir(winget):
                if "ffmpeg" in pkg.lower():
                    pkg_dir = os.path.join(winget, pkg)
                    for root, _dirs, files in os.walk(pkg_dir):
                        if "ffmpeg.exe" in files:
                            candidates.append(os.path.join(root, "ffmpeg.exe"))
        candidates.append(r"C:\ffmpeg\bin\ffmpeg.exe")
        path = next((c for c in candidates if os.path.isfile(c)), None)

    _ffmpeg_path = path or ""
    return _ffmpeg_path or None


def has_ffmpeg() -> bool:
    return find_ffmpeg() is not None


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

    ffmpeg = find_ffmpeg()
    if ffmpeg:
        list_file = os.path.join(tmp_dir, "concat.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for p in paths:
                # concat demuxer 以列表文件所在目录解析相对路径，必须写绝对路径
                f.write(f"file '{os.path.abspath(p).replace(os.sep, '/')}'\n")
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
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
