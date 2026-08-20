"""公共工具：HTTP 请求、状态文件读写、环境变量读取。"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / "state"

# state 文件不存在时的默认值
_STATE_DEFAULTS = {
    "seen": {},
    "candidates": [],
}


def http_get(url, headers=None, timeout=30):
    """GET 请求，返回响应体字节。非 2xx 时抛出带响应体的 RuntimeError。"""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP GET {url} 失败: {e.code} {e.reason}, body: {body[:500]}") from e


def http_post_json(url, payload, headers=None, timeout=30):
    """POST JSON 请求，返回响应体字节。非 2xx 时抛出带响应体的 RuntimeError。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP POST {url} 失败: {e.code} {e.reason}, body: {body[:500]}") from e


def load_state(name):
    """读取 state/{name}.json，文件不存在时返回合理默认值。"""
    path = STATE_DIR / f"{name}.json"
    if not path.exists():
        # 未注册的 name 默认返回空 dict
        return _STATE_DEFAULTS.get(name, {})
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(name, data):
    """写入 state/{name}.json（自动创建目录）。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_env(name, default=None):
    """读取环境变量，缺失且无默认值时抛出清晰错误。"""
    value = os.environ.get(name)
    if value is None or value == "":
        if default is not None:
            return default
        raise RuntimeError(f"缺少必需的环境变量: {name}")
    return value
