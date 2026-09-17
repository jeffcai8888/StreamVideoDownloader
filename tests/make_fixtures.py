"""生成本地测试用的 m3u8 站点：普通 / AES-128 加密 / Master Playlist 三种。

分片用 ffmpeg 生成真实的 MPEG-TS（需 ffmpeg 在 PATH 中）。
"""

import os
import random
import subprocess

from Crypto.Cipher import AES

ROOT = os.path.join(os.path.dirname(__file__), "site")
random.seed(42)


def make_ts(path: str, freq: int) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=25",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=2",
        "-c:v", "libx264", "-c:a", "aac", "-f", "mpegts", path,
    ], check=True)


def pad(data: bytes) -> bytes:
    p = 16 - len(data) % 16
    return data + bytes([p]) * p


os.makedirs(ROOT, exist_ok=True)

# 1. 普通 playlist
plain_dir = os.path.join(ROOT, "plain")
os.makedirs(plain_dir, exist_ok=True)
lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:2", "#EXT-X-MEDIA-SEQUENCE:0"]
for i in range(5):
    make_ts(os.path.join(plain_dir, f"seg{i}.ts"), 440 + i * 100)
    lines.append("#EXTINF:2.0,")
    lines.append(f"seg{i}.ts")
lines.append("#EXT-X-ENDLIST")
with open(os.path.join(plain_dir, "index.m3u8"), "w") as f:
    f.write("\n".join(lines))

# 2. AES-128 加密 playlist（带显式 IV）
enc_dir = os.path.join(ROOT, "enc")
os.makedirs(enc_dir, exist_ok=True)
key = random.randbytes(16)
with open(os.path.join(enc_dir, "key.bin"), "wb") as f:
    f.write(key)
lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:2"]
enc_plain: list[bytes] = []
for i in range(5):
    iv = random.randbytes(16)
    with open(os.path.join(plain_dir, f"seg{i}.ts"), "rb") as g:
        raw = g.read()  # 直接加密普通分片，解密结果应与 expected_plain.ts 一致
    enc_plain.append(raw)  # 下载器解密后会去除 PKCS7 填充，期望值用未填充数据
    data = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(raw))
    with open(os.path.join(enc_dir, f"enc{i}.ts"), "wb") as f:
        f.write(data)
    lines.append(f'#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x{iv.hex()}')
    lines.append("#EXTINF:2.0,")
    lines.append(f"enc{i}.ts")
lines.append("#EXT-X-ENDLIST")
with open(os.path.join(enc_dir, "index.m3u8"), "w") as f:
    f.write("\n".join(lines))

# 3. Master Playlist 指向 plain
with open(os.path.join(ROOT, "master.m3u8"), "w") as f:
    f.write("\n".join([
        "#EXTM3U",
        "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360",
        "plain/index.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720",
        "plain/index.m3u8",
    ]))

# 4. 期望内容（用于校验解密/拼接结果）
with open(os.path.join(ROOT, "expected_plain.ts"), "wb") as f:
    for i in range(5):
        with open(os.path.join(plain_dir, f"seg{i}.ts"), "rb") as g:
            f.write(g.read())
with open(os.path.join(ROOT, "expected_enc.ts"), "wb") as f:
    for chunk in enc_plain:
        f.write(chunk)

print("fixtures 生成于", ROOT)
