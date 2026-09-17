"""B 站视频下载：BV/av 号或视频链接 -> DASH 流（视频+音频）-> ffmpeg 合并为 MP4。

画质说明：未登录（无 SESSDATA Cookie）时 B 站只返回低清晰度（通常 480p 封顶）；
传入登录 Cookie 可解锁 1080p 及以上：
    -H "Cookie: SESSDATA=你的SESSDATA"
"""

import os
import re
import sys

from .downloader import StreamDownloader

PAGELIST_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"
REFERER = "https://www.bilibili.com"

# fnval=4048: 请求 DASH 格式并包含所有可用编码
FNVAL_DASH = 4048

QUALITY_NAMES = {
    127: "8K", 126: "杜比视界", 125: "HDR", 120: "4K", 116: "1080P60",
    112: "1080P+", 80: "1080P", 74: "720P60", 64: "720P", 32: "480P", 16: "360P",
}

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"av(\d+)", re.IGNORECASE)


def is_bilibili(target: str) -> bool:
    return bool(_BV_RE.search(target) or _AV_RE.search(target) or "bilibili.com" in target)


def _extract_ids(target: str) -> dict[str, str]:
    params: dict[str, str] = {}
    m = _BV_RE.search(target)
    if m:
        params["bvid"] = m.group(1)
        return params
    m = _AV_RE.search(target)
    if m:
        params["aid"] = m.group(1)
        return params
    raise ValueError(f"无法从输入中识别 BV/av 号: {target}")


def _get_json(dl: StreamDownloader, url: str, params: dict) -> dict:
    resp = dl.session.get(url, params=params, timeout=dl.opt.timeout,
                          headers={"Referer": REFERER})
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"B站接口返回错误: code={data.get('code')} message={data.get('message')}")
    return data["data"]


def get_video_info(dl: StreamDownloader, target: str, page: int = 1) -> tuple[str, str, dict]:
    """返回 (标题, cid, playurl data)。"""
    # view 接口同时接受 aid/bvid，并返回 bvid + 分 P 列表（playurl 接口只认 bvid）
    view = _get_json(dl, PAGELIST_API, _extract_ids(target))
    pages = view.get("pages") or []
    if not pages:
        raise RuntimeError("未找到视频分 P 信息")
    if page > len(pages):
        raise RuntimeError(f"分 P 超出范围: 共 {len(pages)} P")
    p = pages[page - 1]
    play = _get_json(dl, PLAYURL_API, {
        "bvid": view["bvid"], "cid": p["cid"], "qn": 127, "fnval": FNVAL_DASH, "fourk": 1,
    })
    title = view.get("title", "")
    if len(pages) > 1 and p.get("part"):
        title = f"{title} P{page} {p['part']}"
    return title, str(p["cid"]), play


def _pick_dash_streams(play: dict, quality_id: int | None = None) -> tuple[dict, dict | None]:
    dash = play.get("dash")
    if not dash or not dash.get("video"):
        raise RuntimeError(
            "未获取到 DASH 流。若需要高清晰度，请用 -H \"Cookie: SESSDATA=...\" 传入登录 Cookie"
        )
    if quality_id is not None:
        candidates = [v for v in dash["video"] if v.get("id") == quality_id]
        if not candidates:
            raise RuntimeError(f"所选清晰度不可用: qn={quality_id}")
        # 同清晰度优先 avc（H.264）编码，再按带宽
        video = max(candidates, key=lambda v: ("avc" in v.get("codecs", ""),
                                               v.get("bandwidth", 0)))
    else:
        # 优先高清晰度，同级优先 avc 编码与更高带宽
        video = max(dash["video"], key=lambda v: (v.get("id", 0),
                                                  "avc" in v.get("codecs", ""),
                                                  v.get("bandwidth", 0)))
    audios = dash.get("audio") or []
    audio = max(audios, key=lambda a: a.get("bandwidth", 0)) if audios else None
    return video, audio


def list_qualities(play: dict) -> list[dict]:
    """列出可用的清晰度选项（每个清晰度取 avc 优先的一档），供 GUI/CLI 选择。"""
    dash = play.get("dash") or {}
    best_by_id: dict[int, dict] = {}
    for v in dash.get("video", []):
        qid = v.get("id", 0)
        cur = best_by_id.get(qid)
        if cur is None or ("avc" in v.get("codecs", "")
                           and "avc" not in cur.get("codecs", "")):
            best_by_id[qid] = v
    return [
        {
            "id": qid,
            "label": f"{QUALITY_NAMES.get(qid, f'qn={qid}')} "
                     f"({v.get('width')}x{v.get('height')})",
        }
        for qid, v in sorted(best_by_id.items(), reverse=True)
    ]


def _download_stream(dl: StreamDownloader, url: str, path: str, label: str,
                     on_progress=None) -> None:
    """带进度和断点续传的流式下载。on_progress(label, done_bytes, total_bytes)。"""
    headers = {"Referer": REFERER}
    downloaded = os.path.getsize(path) if os.path.exists(path) else 0
    mode = "wb"
    if downloaded > 0:
        headers["Range"] = f"bytes={downloaded}-"
        mode = "ab"

    last_err: Exception | None = None
    for attempt in range(dl.opt.retries):
        try:
            with dl.session.get(url, headers=headers, timeout=dl.opt.timeout,
                                stream=True) as resp:
                if downloaded and resp.status_code == 200:
                    # 服务端不支持 Range，重新下载
                    downloaded, mode = 0, "wb"
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0)) + downloaded
                with open(path, mode) as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if on_progress:
                            on_progress(label, downloaded, total)
                        elif total:
                            sys.stderr.write(
                                f"\r{label}: {downloaded/1048576:.1f}/{total/1048576:.1f} MB "
                                f"({downloaded/total*100:.1f}%)"
                            )
                            sys.stderr.flush()
            if not on_progress:
                sys.stderr.write("\n")
            return
        except Exception as e:  # noqa: BLE001 - 重试并从断点继续
            last_err = e
            mode = "ab"
            headers["Range"] = f"bytes={downloaded}-"
    raise RuntimeError(f"{label} 下载失败: {last_err}")


def download_bilibili(dl: StreamDownloader, target: str, output: str | None,
                      page: int = 1, keep_temp: bool = False,
                      quality_id: int | None = None, on_progress=None) -> str:
    title, cid, play = get_video_info(dl, target, page)
    if output is None:
        safe = re.sub(r'[\\/:*?"<>|]', "_", title or target).strip() or "bilibili"
        output = f"{safe}.mp4"
    video, audio = _pick_dash_streams(play, quality_id)

    qn = video.get("id", 0)
    quality = QUALITY_NAMES.get(qn, f"qn={qn}")
    print(f"标题: {title or target}")
    print(f"清晰度: {quality} ({video.get('width')}x{video.get('height')}, "
          f"{video.get('codecs', '?')})")
    if qn < 80:
        print("提示: 当前为低清晰度，传入登录 Cookie 可解锁 1080p+:"
              " -H \"Cookie: SESSDATA=...\"")

    tmp_dir = os.path.abspath(".streamdl_bilibili")
    os.makedirs(tmp_dir, exist_ok=True)
    v_path = os.path.join(tmp_dir, "video.m4s")
    a_path = os.path.join(tmp_dir, "audio.m4s")

    _download_stream(dl, video["baseUrl"], v_path, "视频流", on_progress)
    if audio:
        _download_stream(dl, audio["baseUrl"], a_path, "音频流", on_progress)

    from .merger import find_ffmpeg
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(f"未检测到 ffmpeg，无法合并 B 站音视频流。文件保留在 {tmp_dir}")

    import subprocess
    if audio:
        cmd = [ffmpeg, "-y", "-loglevel", "error",
               "-i", v_path, "-i", a_path, "-c", "copy", output]
    else:
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", v_path, "-c", "copy", output]
    subprocess.run(cmd, check=True)

    if not keep_temp:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return output
