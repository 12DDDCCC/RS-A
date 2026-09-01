"""RS-A 桌面应用入口 (PyInstaller 打包目标)。

形态: **独立桌面窗口** (pywebview + 系统 WebView2) 加载 **dsh 网页端
(:3080, RS-A 品牌 + rs_* 插件)** —— 关闭窗口即整个应用退出,
与 dsh 桌面版同款使用直觉。

服务编排 (体验优先: 窗口先出现, 重活全部后台化):
  1. 工作目录锚定 exe 旁; rs-a.env 首次自动生成合法 Fernet 主密钥
  2. 内部线程起后端引擎 FastAPI (:8000, 任务/知识库/凭证)
  3. **立即开窗**展示内置暗色加载页 (不再等 dsh 就绪才见界面)
  4. 后台线程 ensure_dsh_up (:3080 复用/子进程拉起), 就绪后 load_url
     切换到 dsh 界面; 超时则加载人话错误页
  5. 关窗硬退出: closed 事件看门狗 + start() 返回后 os._exit 双保险
     (WebView2 清数据在 frozen 环境可能卡住解释器收尾 → 僵尸进程占 8000)

用法: RS-A.exe            桌面窗口模式 (默认)
      RS-A.exe --browser  浏览器形态 (调试, 打开 dsh 网页端)
"""
from __future__ import annotations

import base64
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import webview
from pathlib import Path

# 开发仓库根 (源码形态探测补丁用; frozen 用 exe 旁候选)
ROOT_DEV = Path(__file__).resolve().parents[2]

DASH_URL = "http://127.0.0.1:3080"

# 内置暗色加载页: 双击 exe 后第一时间可见的界面 (无外部依赖, 离线可用)
LOADING_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html,body{margin:0;height:100%;background:#0f1115;color:#e8eaf0;
    font-family:"Segoe UI","Microsoft YaHei",sans-serif;
    display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px}
  .logo{font-size:30px;font-weight:600;letter-spacing:2px}
  .logo b{color:#4f8cff}
  .spin{width:34px;height:34px;border-radius:50%;
    border:3px solid #2a2f3a;border-top-color:#4f8cff;
    animation:r 1s linear infinite}
  .tip{font-size:13px;color:#8a93a5}
  @keyframes r{to{transform:rotate(360deg)}}
</style></head>
<body>
  <div class="logo">RS<b>-A</b> · 俯瞰世界</div>
  <div class="spin"></div>
  <div class="tip">正在启动遥感引擎，请稍候…</div>
</body></html>"""


def _error_html(reason: str) -> str:
    """dsh 启动失败时的人话错误页 (替代原"界面空白"静默超时)。"""
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html,body{{margin:0;height:100%;background:#0f1115;color:#e8eaf0;
    font-family:"Segoe UI","Microsoft YaHei",sans-serif;
    display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;
    text-align:center;padding:0 40px}}
  .logo{{font-size:26px;font-weight:600;letter-spacing:2px}}
  .logo b{{color:#4f8cff}}
  .reason{{font-size:14px;color:#f0b26b;max-width:560px;line-height:1.7}}
  .tip{{font-size:13px;color:#8a93a5;line-height:1.8}}
</style></head>
<body>
  <div class="logo">RS<b>-A</b></div>
  <div class="reason">{reason}</div>
  <div class="tip">请确认电脑已安装 Node.js 与 dsh (npm install -g @deepseek-ai/dsh)，<br>
  然后关闭本窗口重新打开 RS-A。</div>
</body></html>"""


def _enable_context_menu(window) -> None:
    """打开 WebView2 默认右键菜单 (复制/粘贴)。

    pywebview 把 AreDefaultContextMenusEnabled 绑死在 debug 模式 (49号实测),
    正式包右键无菜单 → 复制只能靠 Ctrl+C。CoreWebView2 属 UI(STA) 线程 ——
    后台线程直访永远 COM 失败, 必须经 WinForms Invoke 派发; 初始化异步,
    未就绪则 0.5s 退避重试 (共 30s)。
    """
    from System import Func, Type

    def _try(tries: int = 0) -> None:
        native = getattr(window, "native", None)
        wv = getattr(native, "webview", None)
        if native is None or wv is None:
            if tries < 60:
                threading.Timer(0.5, lambda: _try(tries + 1)).start()
            return

        done = []

        def _flip() -> None:
            try:
                wv.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True
                done.append(True)
                print("  右键菜单: 已开启 ✓")
            except Exception as e:
                done.append(False)
                if not hasattr(wv, "_rs_logged"):
                    setattr(wv, "_rs_logged", True)
                    print(f"  右键菜单: 首次未就绪 ({type(e).__name__}), 重试中…")

        try:
            native.Invoke(Func[Type](_flip))
        except Exception as e:
            print(f"  右键菜单: Invoke 异常 {type(e).__name__}: {e}")
            done.append(False)

        if not done or not done[0]:
            if tries < 60:
                threading.Timer(0.5, lambda: _try(tries + 1)).start()
            elif tries == 60:
                print("  右键菜单: 30s 重试后仍未开启 (放弃)")

    _try()


def _fernet_ok(key: str) -> bool:
    """key 是否为合法 Fernet 密钥 (32 字节 url-safe base64)。"""
    try:
        from cryptography.fernet import Fernet
        Fernet(key.encode())
        return True
    except Exception:
        return False


def _utf8_console() -> None:
    """GBK 控制台下中文乱码的兜底 (无效环境静默跳过)。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _hard_exit(code: int = 0) -> None:
    """跳过解释器收尾直接退进程: 日志冲刷后 os._exit。

    WebView2 清数据/非守护线程在 frozen 环境可能卡住正常退出路径,
    残留进程会占住 8000 端口导致下次启动报"端口被占用"。
    """
    for s in (sys.stdout, sys.stderr):
        try:
            if s:
                s.flush()
        except Exception:
            pass
    os._exit(code)


def _app_dir() -> Path:
    """exe (或源码运行时仓库根) 所在目录 —— 数据锚点。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _port_busy_dialog() -> None:
    """端口被占的人话提示。windowed 形态无 stdin, input() 必崩 —— 用 Win32 弹窗。"""
    text = ("启动失败: 8000 端口已被占用。\n\n"
            "最常见原因: 已经有一个 RS-A 在运行 (去任务栏找找)，\n"
            "或其他程序占用了 8000 端口。\n\n"
            "处理: 关闭已在运行的 RS-A / 占用程序后, 重新双击 RS-A.exe")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "RS-A 启动失败", 0x30)
    except Exception:
        print(text)


def _force_dark_titlebar(window) -> None:
    """强制深色标题栏 (pywebview 默认跟随系统浅色主题 → 白条)。

    DWM 属性: 20=沉浸式深色(Win10 1809+), 35=标题栏底色, 36=标题文字色
    (35/36 需 Win11 22000+, 失败静默)。底色取应用同款 #0f1115。
    """
    try:
        import ctypes
        hwnd = window.native.Handle.ToInt32()
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        dark = ctypes.c_int(1)
        dwm(hwnd, 20, ctypes.byref(dark), 4)
        cap = ctypes.c_int(0x0015110F)   # COLORREF 0x00BBGGRR = #0f1115
        dwm(hwnd, 35, ctypes.byref(cap), 4)
        txt = ctypes.c_int(0x00FFFFFF)
        dwm(hwnd, 36, ctypes.byref(txt), 4)
    except Exception:
        pass


def _ensure_env(app_dir: Path) -> None:
    """rs-a.env: master key 自动生成 + 已存配置加载 (优先级: 进程 env > 文件)。"""
    env_file = app_dir / "rs-a.env"
    loaded: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                loaded[k.strip()] = v.strip()
    # master key: 不存在/非法则生成合法 Fernet 密钥并持久化 (凭证加密地基)
    have_valid = (_fernet_ok(os.environ.get("REMOTE_SENSING_MASTER_KEY", ""))
                  or _fernet_ok(loaded.get("REMOTE_SENSING_MASTER_KEY", "")))
    if not have_valid:
        loaded["REMOTE_SENSING_MASTER_KEY"] = base64.urlsafe_b64encode(
            secrets.token_bytes(32)).decode()
        env_file.write_text(
            "\n".join(f"{k}={v}" for k, v in loaded.items()) + "\n",
            encoding="utf-8")
    for k, v in loaded.items():
        os.environ.setdefault(k, v)


def _wait_http(url: str, timeout_s: float, interval: float = 0.8) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(interval)
    return False


def _find_dsh_launcher_assets(app_dir: Path) -> tuple[Path | None, Path | None]:
    """定位 node 可执行与 RS-A 品牌补丁 (多候选探测)。

    补丁缺失 = dsh 裸官方形态 (DeepSeek 品牌/预览版/无 rs_* 工具/无凭证面板)。
    frozen 下 __file__ 在 _MEIPASS 临时解包目录, ROOT_DEV 不可用 —— 改查 _MEIPASS。
    """
    node = shutil.which("node")
    cands = [app_dir / "RS-agent" / "dsh" / "cordis.patch.yml"]
    if getattr(sys, "frozen", False):
        internal = Path(getattr(sys, "_MEIPASS", app_dir / "_internal"))
        cands += [internal / "RS-agent" / "dsh" / "cordis.patch.yml",
                  internal / "_internal" / "RS-agent" / "dsh" / "cordis.patch.yml"]
    else:
        cands.append(ROOT_DEV / "RS-agent" / "dsh" / "cordis.patch.yml")
    patch = next((p for p in cands if p.exists()), None)
    return (Path(node) if node else None), patch


def ensure_dsh_up(app_dir: Path) -> bool:
    """确保 dsh 网页端 (:3080, RS-A 品牌) 就绪; 未运行则子进程拉起。

    返回是否就绪 (复用/拉起成功), 由调用方决定加载界面还是错误页。
    """
    if _wait_http(DASH_URL + "/", timeout_s=1):
        print("  dsh 网页端: 已在运行 (复用现有实例)")
        return True

    from src.io.auth import get_access_token

    token = get_access_token("rs-a-user") or ""
    minimax = os.environ.get("MINIMAX_API_KEY", "")
    env = {**os.environ,
           "REMOTE_SENSING_TOKEN": token,
           "MINIMAX_API_KEY": minimax,
           # 插件落盘围栏根目录: 跟随 exe 位置 (补丁里缺省回退开发仓库路径)
           "RS_WORKSPACE": str(app_dir)}

    node, patch = _find_dsh_launcher_assets(app_dir)
    if not node or not patch:
        print("  ⚠ 未找到 node/dsh 或品牌补丁, 尝试按 PATH 直接拉起…")
        cmd = ["dsh", "--profile", "web", "--no-open"]
    else:
        cmd = ["dsh", "--patch", str(patch),
               "--profile", "web", "--no-open"]
    print("  正在拉起 dsh 网页端 …")
    subprocess.Popen(cmd, env=env, cwd=str(app_dir),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     shell=(os.name == "nt"))
    if _wait_http(DASH_URL + "/", timeout_s=120):
        return True
    print("  ⚠ dsh 启动超时")
    return False


def main() -> None:
    browser_mode = "--browser" in sys.argv
    _utf8_console()
    app_dir = _app_dir()
    os.chdir(app_dir)          # cache/ jobs.db runs logs 全部落 exe 旁
    _ensure_env(app_dir)
    # 打包形态下 src 包在 _internal, 把它加进 import 路径
    if getattr(sys, "frozen", False):
        internal = Path(getattr(sys, "_MEIPASS", app_dir / "_internal"))
        for cand in (internal, internal / "_internal"):
            if (cand / "src").exists():
                sys.path.insert(0, str(cand))
                break

    # 端口占用预检: 上一实例关窗收尾时会短暂占用, 最多等 8s 让位再报
    deadline = time.time() + 8
    while not _port_free(8000):
        if time.time() >= deadline:
            _port_busy_dialog()
            return
        time.sleep(0.5)

    print("=" * 52)
    print("  RS-A · 遥感分析桌面版")
    print(f"  数据目录: {app_dir}")
    print("  服务地址: http://127.0.0.1:8000 (内部通道)")
    print("=" * 52)

    import uvicorn

    def _serve() -> None:
        # 桌面模式无控制台: uvicorn/异常日志改落文件 (监测通道保留)
        if not browser_mode:
            log_dir = Path("cache") / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = open(log_dir / "rs-a-app.log", "a",
                      encoding="utf-8", errors="replace")
            sys.stdout = fh
            sys.stderr = fh
        import src.main as _m
        uvicorn.run(_m.app, host="127.0.0.1", port=8000, log_level="info")

    server_thread = threading.Thread(target=_serve, daemon=True)
    server_thread.start()

    if browser_mode:
        webbrowser.open(DASH_URL)
        server_thread.join()
        return

    # 体验核心: 窗口立刻出现 (暗色加载页), dsh 编排全部后台化
    # text_select=True: pywebview 默认给 body 注入 user-select:none (整窗禁选),
    # 不开则聊天内容无法选中复制 (49号)
    window = webview.create_window(
        "RS-A · 俯瞰世界",
        html=LOADING_HTML,
        width=1366, height=850, min_size=(960, 620),
        background_color="#0f1115",
        text_select=True,
    )

    def _on_shown() -> None:
        _force_dark_titlebar(window)
        _enable_context_menu(window)

    window.events.shown += _on_shown

    def _bootstrap() -> None:
        if ensure_dsh_up(app_dir):
            window.load_url(DASH_URL)
        else:
            window.load_html(_error_html(
                "dsh 网页端启动超时 (需 Node.js 与 dsh 环境)"))

    # 关窗硬退出看门狗: closed 事件后 3s 仍在 = WebView2 收尾卡死, 强制退
    def _exit_watchdog() -> None:
        window.events.closed.wait()
        time.sleep(3)
        _hard_exit()

    threading.Thread(target=_exit_watchdog, daemon=True).start()
    webview.start(_bootstrap)          # 阻塞至用户关闭窗口
    _hard_exit()                       # start() 返回即窗口已关: 立即放行 8000


if __name__ == "__main__":
    main()
