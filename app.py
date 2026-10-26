# -*- coding: utf-8 -*-
"""
france2subs 图形界面

给不懂命令行的普通用户：粘贴 france.tv 网址 → 点「开始」→ 看进度 → 完成。
产出视频 + 法语/中文/中法双语字幕，与视频同目录。
"""
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

HERE = Path(__file__).resolve().parent
ENGINE = HERE / "france2subs.py"


class App:
    def __init__(self, root):
        self.root = root
        root.title("france2subs — 下载 france.tv 视频并配字幕")
        root.geometry("760x560")
        root.minsize(640, 480)

        self.q = queue.Queue()
        self.proc = None

        self._build_ui()
        self._poll_queue()

    def _build_ui(self):
        pad = dict(padx=12, pady=6)

        # 标题
        title = ttk.Label(self.root, text="france.tv 视频下载 + 中法双语字幕",
                          font=("Microsoft YaHei UI", 14, "bold"))
        title.pack(anchor="w", **pad)

        # URL 输入
        url_frame = ttk.Frame(self.root)
        url_frame.pack(fill="x", **pad)
        ttk.Label(url_frame, text="视频网址：").pack(side="left")
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(url_frame, textvariable=self.url_var)
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.url_entry.bind("<Return>", lambda e: self.start())

        # 选项行
        opt_frame = ttk.Frame(self.root)
        opt_frame.pack(fill="x", **pad)

        ttk.Label(opt_frame, text="字幕类型：").pack(side="left")
        self.sub_var = tk.StringVar(value="中法双语")
        sub_box = ttk.Combobox(opt_frame, textvariable=self.sub_var, width=10,
                               state="readonly", values=["中法双语", "仅法语", "仅中文", "全部"])
        sub_box.pack(side="left", padx=(4, 16))

        ttk.Label(opt_frame, text="画质：").pack(side="left")
        self.height_var = tk.StringVar(value="1080p")
        h_box = ttk.Combobox(opt_frame, textvariable=self.height_var, width=8,
                             state="readonly", values=["1080p", "720p", "540p", "360p"])
        h_box.pack(side="left", padx=(4, 16))

        ttk.Label(opt_frame, text="保存到：").pack(side="left")
        self.out_var = tk.StringVar(value=str(HERE.parent))
        out_entry = ttk.Entry(opt_frame, textvariable=self.out_var, width=30)
        out_entry.pack(side="left", padx=(4, 4))
        ttk.Button(opt_frame, text="浏览…", command=self._browse).pack(side="left")

        # 按钮行
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btn_frame, text="▶ 开始下载并配字幕",
                                    command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btn_frame, text="■ 停止", command=self.stop,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        # 进度条
        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", **pad)

        # 状态标签
        self.status_var = tk.StringVar(value="就绪。粘贴网址后点「开始」。")
        ttk.Label(self.root, textvariable=self.status_var, foreground="#555").pack(
            anchor="w", padx=12)

        # 日志
        ttk.Label(self.root, text="运行日志：").pack(anchor="w", padx=12, pady=(6, 0))
        self.log = scrolledtext.ScrolledText(self.root, height=14, state="disabled",
                                             font=("Consolas", 9), wrap="word")
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get())
        if d:
            self.out_var.set(d)

    def _append_log(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self):
        url = self.url_var.get().strip()
        if not url.startswith("http"):
            messagebox.showwarning("提示", "请先粘贴有效的视频网址（http/https 开头）。")
            return

        sub_map = {"中法双语": "bilingual", "仅法语": "fr", "仅中文": "zh", "全部": "all"}
        height_map = {"1080p": 1080, "720p": 720, "540p": 540, "360p": 360}
        sub = sub_map.get(self.sub_var.get(), "bilingual")
        h = height_map.get(self.height_var.get(), 1080)
        out = self.out_var.get().strip() or str(HERE.parent)

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress.start(12)
        self.status_var.set("运行中，请稍候…")
        self._clear_log()

        cmd = [sys.executable, str(ENGINE), url,
               "--subtitle", sub, "--max-height", str(h), "--out", out]

        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )

        def reader():
            for line in self.proc.stdout:
                self.q.put(("log", line.rstrip("\n")))
            self.proc.wait()
            self.q.put(("done", self.proc.returncode))

        threading.Thread(target=reader, daemon=True).start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self._append_log("\n[已请求停止]")
            self.status_var.set("正在停止…")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "done":
                    self._on_done(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _on_done(self, code):
        self.progress.stop()
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if code == 0:
            self.status_var.set("✅ 完成！字幕已生成在保存目录中。")
            messagebox.showinfo("完成", "视频和字幕已生成完毕。\n\n字幕文件（.srt）与视频同名同目录，\n用 VLC / PotPlayer 打开视频即可自动加载字幕。")
        else:
            self.status_var.set("❌ 出错，请查看日志。")
            messagebox.showerror("出错", "处理失败，请查看日志了解原因。")


def main():
    root = tk.Tk()
    # 尝试设置中文字体，兼容不同系统
    try:
        root.tk.call("tk", "scaling", 1.2)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
