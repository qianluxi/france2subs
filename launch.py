# -*- coding: utf-8 -*-
"""france2subs 启动器：找一个能跑图形界面的 Python，然后打开界面。

为什么需要它：Windows 上常见的坑是
  · `python` 指向的是某个精简版 / 微软商店版解释器，没有 tkinter，界面起不来；
  · 依赖装在了 A 解释器上，但带 tkinter 的是 B 解释器。
本脚本会枚举机器上的候选解释器，逐个检查 tkinter 与依赖，挑一个能用的启动。

用法（一般由 start.bat 调用）：
    python launch.py            # 启动图形界面
    python launch.py --console  # 在当前窗口前台运行（看报错用）
    python launch.py --check    # 只体检，不启动
"""
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE / "app.py"
REQ = HERE / "requirements.txt"

MODULES = ["tkinter", "yt_dlp", "faster_whisper", "argostranslate", "static_ffmpeg"]
DEP_MODULES = MODULES[1:]

PROBE_CODE = (
    "import importlib.util as u, json\n"
    f"mods={MODULES!r}\n"
    "print(json.dumps({m: bool(u.find_spec(m)) for m in mods}))\n"
)


def setup_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _candidate_interpreters():
    """按优先级收集候选解释器命令（每个是参数列表）。"""
    out = []
    seen = set()

    def add(cmd):
        exe = cmd[0]
        path = shutil.which(exe) if not os.path.isabs(exe) else exe
        if not path or not os.path.exists(path):
            return
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        out.append(cmd)

    # 官方启动器 py -3（装官方 Python 时自带，通常带 tkinter）
    if shutil.which("py"):
        add(["py", "-3"])

    # 当前解释器（可能没有 tkinter）
    add([sys.executable])

    # PATH 里的 python
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p:
            add([p])

    # 常见安装位置
    patterns = [
        r"%LOCALAPPDATA%\Programs\Python\Python3*\python.exe",
        r"C:\Python3*\python.exe",
        r"%ProgramFiles%\Python3*\python.exe",
    ]
    for pat in patterns:
        expanded = os.path.expandvars(pat)
        if "%" in expanded:
            continue
        for p in sorted(glob.glob(expanded)):
            add([p])

    return out


def _probe(cmd):
    """返回该解释器的模块可用情况；失败返回 None。"""
    try:
        p = subprocess.run([str(c) for c in cmd] + ["-c", PROBE_CODE],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            return None
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return None


def _real_exe(cmd):
    """把 `py -3` 这类启动器换成真实的 python.exe 路径。"""
    try:
        p = subprocess.run([str(c) for c in cmd] + ["-c", "import sys;print(sys.executable)"],
                           capture_output=True, text=True, timeout=60)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip().splitlines()[-1]
    except Exception:
        pass
    return str(cmd[0])


def scan():
    """体检所有候选，返回 (可用列表, 详情列表)。"""
    usable, detail = [], []
    seen_exe = set()
    for cmd in _candidate_interpreters():
        info = _probe(cmd)
        if info is None:
            continue
        exe = _real_exe(cmd)
        key = os.path.normcase(os.path.abspath(exe))
        if key in seen_exe:          # py -3 和 python.exe 可能指向同一个解释器
            continue
        seen_exe.add(key)
        has_tk = info.get("tkinter", False)
        missing = [m for m in DEP_MODULES if not info.get(m)]
        detail.append({"cmd": cmd, "exe": exe, "tk": has_tk, "missing": missing})
        if has_tk and not missing:
            usable.append({"cmd": cmd, "exe": exe})
    return usable, detail


def print_report(detail):
    if not detail:
        print("  [错误] 没有找到可用的 Python 解释器。")
        print("         请安装 Python 3.9+（python.org），安装时勾选 Add Python to PATH。")
        return
    print("  检测到的 Python：")
    for d in detail:
        tk = "tkinter OK" if d["tk"] else "无 tkinter"
        dep = "依赖完整" if not d["missing"] else "缺: " + ",".join(d["missing"])
        print(f"    · {d['exe']}")
        print(f"        {tk} / {dep}")


def install_deps(exe):
    print(f"\n  正在为它安装依赖：{exe}")
    print(f"  命令：{exe} -m pip install -r requirements.txt\n")
    try:
        rc = subprocess.call([exe, "-m", "pip", "install", "-r", str(REQ)])
    except Exception as e:
        print(f"  [错误] 安装失败：{e}")
        return False
    return rc == 0


def launch(exe, console=False):
    """启动图形界面。console=True 时保留控制台窗口（便于看报错）。"""
    if console:
        print(f"\n  启动（前台）：{Path(exe).name} app.py")
        print("  关闭窗口即退出。\n")
        return subprocess.call([exe, str(APP)], cwd=str(HERE))

    pyw = Path(exe).with_name("pythonw.exe")
    target = str(pyw) if pyw.exists() else exe
    print(f"\n  启动：{Path(target).name} app.py")
    flags = 0
    if not target.lower().endswith("pythonw.exe"):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([target, str(APP)], cwd=str(HERE), creationflags=flags)
    return 0


def main(argv):
    setup_stdout()
    console = "--console" in argv
    check_only = "--check" in argv

    print("=" * 46)
    print("  france2subs 启动检查")
    print("=" * 46)

    usable, detail = scan()
    print_report(detail)

    if usable:
        if check_only:
            print("\n  [OK] 有可用的 Python，可以直接启动。")
            return 0
        return launch(usable[0]["exe"], console=console)

    if check_only:
        return 1

    # 没有「既有 tkinter 又有依赖」的解释器，尝试补救
    tk_only = [d for d in detail if d["tk"]]
    if tk_only:
        cand = tk_only[0]
        print(f"\n  找到一个带图形界面的 Python，但缺少运行依赖：")
        print(f"    {cand['exe']}")
        print(f"    缺少：{', '.join(cand['missing'])}")
        try:
            ans = input("\n  现在为它安装依赖吗？[Y/n] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("n", "no"):
            if install_deps(cand["exe"]):
                print("\n  [完成] 依赖装好了，重新启动。")
                return launch(cand["exe"], console=console)
            print("\n  [错误] 依赖安装失败，请检查网络后重试。")
            return 1
        return 1

    # 所有解释器都没有 tkinter
    print("\n  [错误] 现有 Python 都没有 tkinter（图形界面库）。")
    print("         请安装官方 Python：https://www.python.org/downloads/")
    print("         安装时勾选 Add Python to PATH，然后重跑 install.bat。")
    print("\n  提示：只想用命令行版可以执行")
    print("        python france2subs.py \"视频网址\"")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
