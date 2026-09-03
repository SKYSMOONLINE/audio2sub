import sys
import os
import time
import ctypes
import threading
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

import config  # noqa: F401
import uvicorn
import webview
from web.server import app

WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010

def run_backend():
    uvicorn.run(app, host="127.0.0.1", port=8710, log_level="warning")

def wait_server(timeout=10.0):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8710/api/health", timeout=0.5) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False

def setup_window_chrome():
    """在窗口启动后，通过 Win32 API 注入自定义高分辨率图标与暗黑标题栏"""
    time.sleep(0.4)
    icon_path = str(APP_DIR / "icon.ico")
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    
    for _ in range(30):
        hwnd = user32.FindWindowW(None, "audio2sub - 音声字幕工作台")
        if hwnd:
            # 1. 注入自定义窗口图标 (解决默认方块图标问题)
            if os.path.exists(icon_path):
                hicon_sm = user32.LoadImageW(None, icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                hicon_lg = user32.LoadImageW(None, icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
                if hicon_sm:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_sm)
                if hicon_lg:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_lg)
            
            # 2. 注入沉浸式暗黑模式标题栏
            val = ctypes.c_int(1)
            res = dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
            if res != 0:
                dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(val), ctypes.sizeof(val))
            break
        time.sleep(0.2)

def main():
    server_thread = threading.Thread(target=run_backend, daemon=True)
    server_thread.start()
    wait_server()

    chrome_thread = threading.Thread(target=setup_window_chrome, daemon=True)
    chrome_thread.start()

    window = webview.create_window(
        title="audio2sub - 音声字幕工作台",
        url="http://127.0.0.1:8710",
        width=1280,
        height=820,
        min_size=(1020, 680),
        background_color="#08090c",
    )
    webview.start()

if __name__ == "__main__":
    main()
