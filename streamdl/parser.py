"""m3u8 (HLS) 播放列表解析。

支持 Master Playlist（多码率选择）与 Media Playlist（分片列表），
解析 EXT-X-KEY (AES-128)、EXT-X-MAP (fMP4 初始化段)、EXT-X-BYTERANGE 等常见标签。
"""

from dataclasses import dataclass, field
from urllib.parse import urljoin


@dataclass
class KeyInfo:
    method: str          # NONE / AES-128 / SAMPLE-AES
    uri: str = ""
    iv: bytes | None = None


@dataclass
class Segment:
    uri: str
    duration: float = 0.0
    title: str = ""
    key: KeyInfo | None = None
    byte_range: tuple[int, int] | None = None  # (length, offset)
    discontinuity: bool = False
    # 下载后填充
    index: int = 0
    path: str = ""


@dataclass
class MediaPlaylist:
    segments: list[Segment] = field(default_factory=list)
    target_duration: float = 0.0
    media_sequence: int = 0
    endlist: bool = False
    map_uri: str = ""            # fMP4 初始化段
    map_iv: bytes | None = None
    is_master: bool = False


@dataclass
class Variant:
    uri: str
    bandwidth: int = 0
    resolution: str = ""
    codecs: str = ""


def _parse_attrs(s: str) -> dict[str, str]:
    """解析逗号分隔的 KEY=VALUE 属性列表（VALUE 可为带引号字符串）。"""
    attrs: dict[str, str] = {}
    key, val, buf = "", "", []
    in_quotes, reading_key = False, True
    for ch in s:
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if reading_key and ch == "=":
            key = "".join(buf).strip()
            buf = []
            reading_key = False
            continue
        if not in_quotes and ch == ",":
            attrs[key] = "".join(buf).strip()
            key, buf, reading_key = "", [], True
            continue
        buf.append(ch)
    if not reading_key:
        attrs[key] = "".join(buf).strip()
    return attrs


def _parse_key(tag_value: str, base_url: str) -> KeyInfo:
    attrs = _parse_attrs(tag_value)
    iv_hex = attrs.get("IV", "")
    iv = bytes.fromhex(iv_hex[2:] if iv_hex.lower().startswith("0x") else iv_hex) if iv_hex else None
    return KeyInfo(
        method=attrs.get("METHOD", "NONE"),
        uri=urljoin(base_url, attrs.get("URI", "").strip('"')),
        iv=iv,
    )


def parse_m3u8(text: str, base_url: str) -> MediaPlaylist | list[Variant]:
    """解析 m3u8 文本。

    返回 Master Playlist 的变体列表（list[Variant]）或 MediaPlaylist。
    base_url 用于将相对 URI 解析为绝对 URL。
    """
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise ValueError("不是合法的 m3u8 文件（缺少 #EXTM3U 头）")

    variants: list[Variant] = []
    playlist = MediaPlaylist()

    current_key: KeyInfo | None = None
    current_duration = 0.0
    current_title = ""
    current_range: tuple[int, int] | None = None
    discontinuity = False
    range_offset = 0
    pending_stream_inf: dict[str, str] | None = None

    for line in lines[1:]:
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending_stream_inf = _parse_attrs(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            playlist.target_duration = float(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            playlist.media_sequence = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-KEY:"):
            key = _parse_key(line.split(":", 1)[1], base_url)
            current_key = None if key.method.upper() == "NONE" else key
        elif line.startswith("#EXT-X-MAP:"):
            attrs = _parse_attrs(line.split(":", 1)[1])
            playlist.map_uri = urljoin(base_url, attrs.get("URI", "").strip('"'))
            if "IV" in attrs:
                iv_hex = attrs["IV"]
                playlist.map_iv = bytes.fromhex(iv_hex[2:] if iv_hex.lower().startswith("0x") else iv_hex)
        elif line.startswith("#EXT-X-BYTERANGE:"):
            spec = line.split(":", 1)[1]
            if "@" in spec:
                length, offset = spec.split("@", 1)
                current_range = (int(length), int(offset))
            else:
                current_range = (int(spec), range_offset)
        elif line.startswith("#EXTINF:"):
            body = line.split(":", 1)[1]
            dur, _, title = body.partition(",")
            current_duration = float(dur)
            current_title = title
        elif line == "#EXT-X-DISCONTINUITY":
            discontinuity = True
        elif line.startswith("#EXT-X-ENDLIST"):
            playlist.endlist = True
        elif line.startswith("#"):
            pass  # 其余标签忽略
        else:
            url = urljoin(base_url, line)
            if pending_stream_inf is not None:
                variants.append(Variant(
                    uri=url,
                    bandwidth=int(pending_stream_inf.get("BANDWIDTH", "0") or 0),
                    resolution=pending_stream_inf.get("RESOLUTION", ""),
                    codecs=pending_stream_inf.get("CODECS", ""),
                ))
                pending_stream_inf = None
            else:
                seg = Segment(
                    uri=url,
                    duration=current_duration,
                    title=current_title,
                    key=current_key,
                    byte_range=current_range,
                    discontinuity=discontinuity,
                    index=len(playlist.segments),
                )
                playlist.segments.append(seg)
                if current_range:
                    range_offset = current_range[1] + current_range[0]
                current_duration, current_title = 0.0, ""
                current_range, discontinuity = None, False

    if variants:
        return variants
    return playlist


def pick_best_variant(variants: list[Variant]) -> Variant:
    """从 Master Playlist 中选取码率最高的变体。"""
    return max(variants, key=lambda v: v.bandwidth)
