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
    p.add_argument("--with-cover", action="store_true", help="B 站视频同时下载封面图片")
    p.add_argument("--with-mp3", action="store_true", help="B 站视频同时提取 MP3 音频")
    p.add_argument("--login", action="store_true",
                   help="B 站扫码登录（终端显示二维码），登录态保存后无需再输入 SESSDATA")
    p.add_argument("--cookies", metavar="FILE",
                   help="cookies.txt 文件路径（YouTube/抖音等站点验证需要时）")
    return p


def _ytdlp_download(args) -> int:
    """非 m3u8 链接（YouTube / 抖音等）交给 yt-dlp 引擎。"""
    from .ytdlp_bridge import download as ytdlp_download
    from .ytdlp_bridge import list_qualities

    def _progress(label, done, total):
        if total:
            sys.stderr.write(f"\r{label}: {done/1048576:.1f}/{total/1048576:.1f} MB "
                             f"({done/total*100:.1f}%)")
        else:
            sys.stderr.write(f"\r{label}: {done/1048576:.1f} MB")
        sys.stderr.flush()

    try:
        print("使用 yt-dlp 引擎解析（YouTube / 抖音等）...")
        title, qualities = list_qualities(args.url, proxy=args.proxy,
                                          cookiefile=args.cookies)
        print(f"标题: {title}")
        if args.select and len(qualities) > 1:
            for i, q in enumerate(qualities):
                print(f"  [{i}] {q['label']}")
            idx = int(input("请选择序号: ").strip())
            chosen = qualities[idx]
        else:
            chosen = qualities[0]
        print(f"已选择: {chosen['label']}")
        out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
        final = ytdlp_download(args.url, out_dir, format_id=chosen["id"],
                               proxy=args.proxy, cookiefile=args.cookies,
                               on_progress=_progress)
        sys.stderr.write("\n")
    except Exception as e:  # noqa: BLE001
        print(f"\n下载失败: {e}", file=sys.stderr)
        return 1
    size_mb = os.path.getsize(final) / 1024 / 1024
    print(f"完成: {final} ({size_mb:.1f} MB)")
    return 0


def _do_login() -> int:
    """终端扫码登录 B 站：打印 ASCII 二维码，手机确认后保存 SESSDATA。"""
    from .bili_login import (WAITING_CONFIRM, generate_qrcode, render_qr_ascii,
                             wait_login)
    try:
        key, url = generate_qrcode()
        print("请使用哔哩哔哩手机 App 扫描下方二维码并确认登录：\n")
        print(render_qr_ascii(url))

        def _status(code):
            if code == WAITING_CONFIRM:
                print("\r已扫码，请在手机上确认登录...", end="", flush=True)

        sessdata = wait_login(key, on_status=_status)
        print(f"\n登录成功，SESSDATA 已保存（{sessdata[:8]}...）")
        print("之后下载 B 站视频会自动使用该登录态，无需再手动输入")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"\n登录失败: {e}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gui:
        from .gui import run
        return run()

    if args.login:
        return _do_login()

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
        # 未显式传 Cookie 时自动读取扫码登录保存的 SESSDATA
        if not (opt.headers and "Cookie" in opt.headers):
            from .bili_login import load_sessdata
            saved = load_sessdata()
            if saved:
                dl.session.headers["Cookie"] = f"SESSDATA={saved}"
                print("已自动使用保存的 B 站登录态")
        output = None if args.output == "output.mp4" else args.output  # 默认用视频标题命名
        try:
            final = download_bilibili(dl, args.url, output,
                                      page=args.page, keep_temp=args.keep_temp,
                                      with_cover=args.with_cover,
                                      with_mp3=args.with_mp3)
        except Exception as e:  # noqa: BLE001
            print(f"B站下载失败: {e}", file=sys.stderr)
            return 1
        size_mb = os.path.getsize(final) / 1024 / 1024
        print(f"完成: {final} ({size_mb:.1f} MB)")
        return 0

    # 先按 m3u8 尝试；不是 m3u8 则交给 yt-dlp（YouTube / 抖音等）
    print(f"获取播放列表: {args.url}")
    result = None
    try:
        text = dl.fetch_text(args.url)
        try:
            result = parse_m3u8(text, args.url)
        except ValueError:
            result = None
    except Exception:  # noqa: BLE001 - 非 m3u8 页面（HTML/重定向）属正常情况
        result = None

    if result is None:
        return _ytdlp_download(args)

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
