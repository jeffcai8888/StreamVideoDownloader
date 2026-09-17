"""命令行入口。"""

import argparse
import hashlib
import os
import shutil
import sys

from .downloader import DEFAULT_UA, DownloadOptions, StreamDownloader
from .merger import has_ffmpeg, merge
from .parser import MediaPlaylist, parse_m3u8, pick_best_variant


def _parse_header(items: list[str]) -> dict[str, str]:
    headers = {}
    for item in items or []:
        if ":" not in item:
            raise argparse.ArgumentTypeError(f"Header 格式应为 'Key: Value': {item}")
        k, v = item.split(":", 1)
        headers[k.strip()] = v.strip()
    return headers


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="streamdl",
        description="流媒体 (HLS/m3u8) 下载工具：多线程下载、AES-128 解密、自动合并",
    )
    p.add_argument("url", nargs="?", help="m3u8 地址，或 B 站 BV 号 / av 号 / 视频链接")
    p.add_argument("--gui", action="store_true", help="启动图形界面")
    p.add_argument("-o", "--output", default="output.mp4",
                   help="输出文件名（默认 output.mp4；无 ffmpeg 时自动改为 .ts）")
    p.add_argument("-p", "--page", type=int, default=1, help="B 站分 P 序号（默认 1）")
    p.add_argument("-t", "--threads", type=int, default=8, help="并发线程数（默认 8）")
    p.add_argument("--retries", type=int, default=5, help="单分片重试次数（默认 5）")
    p.add_argument("--timeout", type=float, default=30.0, help="网络超时秒数（默认 30）")
    p.add_argument("-H", "--header", action="append", metavar="'Key: Value'",
                   help="自定义请求头，可多次指定，如 -H 'Referer: https://x.com'")
    p.add_argument("--proxy", help="HTTP 代理，如 http://127.0.0.1:7890")
    p.add_argument("--workdir", default=None, help="临时分片目录（默认按 URL 自动生成）")
    p.add_argument("--keep-temp", action="store_true", help="合并后保留临时分片目录")
    p.add_argument("--select", action="store_true",
                   help="Master Playlist 时列出所有清晰度供选择（默认自动选最高码率）")
    p.add_argument("--live", action="store_true",
                   help="直播录制模式：持续轮询 m3u8 直到 ENDLIST 或 Ctrl+C")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gui:
        from .gui import run
        return run()
    if not args.url:
        build_parser().error("缺少 url 参数（或使用 --gui 启动图形界面）")

    opt = DownloadOptions(
        threads=args.threads,
        retries=args.retries,
        timeout=args.timeout,
        headers=_parse_header(args.header),
        proxy=args.proxy,
        keep_temp=args.keep_temp,
    )
    dl = StreamDownloader(opt)

    from .bilibili import download_bilibili, is_bilibili
    if is_bilibili(args.url):
        output = None if args.output == "output.mp4" else args.output  # 默认用视频标题命名
        try:
            final = download_bilibili(dl, args.url, output,
                                      page=args.page, keep_temp=args.keep_temp)
        except Exception as e:  # noqa: BLE001
            print(f"B站下载失败: {e}", file=sys.stderr)
            return 1
        size_mb = os.path.getsize(final) / 1024 / 1024
        print(f"完成: {final} ({size_mb:.1f} MB)")
        return 0

    print(f"获取播放列表: {args.url}")
    try:
        text = dl.fetch_text(args.url)
    except Exception as e:  # noqa: BLE001
        print(f"获取失败: {e}", file=sys.stderr)
        return 1

    try:
        result = parse_m3u8(text, args.url)
    except ValueError as e:
        print(f"解析失败: {e}", file=sys.stderr)
        return 1

    media_url = args.url  # 若为 Master Playlist 则随后替换为解析出的变体地址

    # Master Playlist -> 选择清晰度
    if not isinstance(result, MediaPlaylist):
        variants = result
        if args.select:
            for i, v in enumerate(variants):
                print(f"  [{i}] {v.resolution or '未知分辨率'}  "
                      f"bandwidth={v.bandwidth}  codecs={v.codecs}")
            idx = int(input("请选择序号: ").strip())
            chosen = variants[idx]
        else:
            chosen = pick_best_variant(variants)
        print(f"已选择: {chosen.resolution or '未知分辨率'} bandwidth={chosen.bandwidth}")
        text = dl.fetch_text(chosen.uri)
        result = parse_m3u8(text, chosen.uri)
        if not isinstance(result, MediaPlaylist):
            print("嵌套 Master Playlist，暂不支持", file=sys.stderr)
            return 1
        media_url = chosen.uri

    playlist: MediaPlaylist = result
    duration = sum(s.duration for s in playlist.segments)

    workdir = args.workdir or os.path.join(
        ".streamdl_tmp", hashlib.md5(args.url.encode()).hexdigest()[:12]
    )

    if args.live:
        try:
            playlist = dl.download_live(media_url, workdir)
        except RuntimeError as e:
            print(f"录制失败: {e}", file=sys.stderr)
            return 1
        print(f"共录制 {len(playlist.segments)} 个分片")
    else:
        print(f"共 {len(playlist.segments)} 个分片，时长约 {duration:.1f} 秒")
        if any(s.key for s in playlist.segments):
            print("检测到 AES-128 加密，将自动解密")
        try:
            dl.download_playlist(playlist, workdir)
        except RuntimeError as e:
            print(f"下载未完成: {e}", file=sys.stderr)
            return 1

    print(f"合并分片 -> {args.output}" + ("" if has_ffmpeg() else "（未检测到 ffmpeg，输出 TS）"))
    try:
        final = merge(playlist, workdir, args.output)
    except Exception as e:  # noqa: BLE001
        print(f"合并失败: {e}（分片保留在 {workdir}，可手动合并）", file=sys.stderr)
        return 1

    if not args.keep_temp:
        shutil.rmtree(workdir, ignore_errors=True)

    size_mb = os.path.getsize(final) / 1024 / 1024
    print(f"完成: {final} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
