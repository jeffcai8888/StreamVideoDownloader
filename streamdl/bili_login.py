"""B 站扫码登录：生成二维码 -> 轮询登录状态 -> 保存/读取 SESSDATA。

SESSDATA 持久化在 ~/.streamdl_config.json，CLI 与 GUI 共用。
"""

import json
import os
import time

import requests

GENERATE_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".streamdl_config.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 轮询状态码
WAITING_SCAN = 86101    # 未扫码
WAITING_CONFIRM = 86090  # 已扫码，待手机确认
EXPIRED = 86038          # 二维码已过期
SUCCESS = 0


def load_sessdata() -> str:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f).get("sessdata", "")
    except Exception:  # noqa: BLE001 - 配置缺失/损坏时视为未登录
        return ""


def save_sessdata(sessdata: str) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"sessdata": sessdata}, f)


def clear_sessdata() -> None:
    try:
        os.remove(CONFIG_FILE)
    except OSError:
        pass


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def generate_qrcode(session: requests.Session | None = None) -> tuple[str, str]:
    """生成登录二维码，返回 (qrcode_key, 二维码内容 URL)。"""
    s = session or _new_session()
    resp = s.get(GENERATE_API, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"生成二维码失败: {data.get('message')}")
    return data["data"]["qrcode_key"], data["data"]["url"]


def poll_once(session: requests.Session, qrcode_key: str) -> int:
    """轮询一次登录状态，返回状态码。成功时 session 自动带上 SESSDATA。"""
    resp = session.get(POLL_API, params={"qrcode_key": qrcode_key}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"轮询登录状态失败: {data.get('message')}")
    return data["data"]["code"]


def extract_sessdata(session: requests.Session) -> str:
    return session.cookies.get("SESSDATA", "", domain=".bilibili.com") or \
        session.cookies.get("SESSDATA", "")


def wait_login(qrcode_key: str, on_status=None, timeout: float = 180.0) -> str:
    """轮询直到登录成功/过期/超时，返回 SESSDATA 并持久化。

    on_status(code) 用于 UI 状态提示；成功返回 SESSDATA，失败抛异常。
    """
    session = _new_session()
    deadline = time.time() + timeout
    while time.time() < deadline:
        code = poll_once(session, qrcode_key)
        if on_status:
            on_status(code)
        if code == SUCCESS:
            sessdata = extract_sessdata(session)
            if not sessdata:
                raise RuntimeError("登录成功但未获取到 SESSDATA")
            save_sessdata(sessdata)
            return sessdata
        if code == EXPIRED:
            raise RuntimeError("二维码已过期，请重新生成")
        time.sleep(2)
    raise RuntimeError("登录超时，请重试")


def render_qr_png(url: str) -> bytes:
    """把二维码内容渲染为 PNG 字节（供 GUI 显示）。"""
    import io

    import qrcode

    img = qrcode.make(url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_qr_ascii(url: str) -> str:
    """把二维码渲染为终端文本（供 CLI 显示）。

    使用纯 ASCII 的 ##/空格，避免 GBK 控制台无法编码半块字符的问题。
    """
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make()
    return "\n".join(
        "".join("##" if cell else "  " for cell in row)
        for row in qr.get_matrix()
    )
