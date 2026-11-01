# -*- coding: utf-8 -*-
"""
france2subs —— 下载 france.tv 视频并自动生成法语/中法双语字幕

用法：
    python france2subs.py <URL> [--subtitle fr|bilingual] [--max-height 1080] [--out DIR]

产出（与视频同名，放在 --out 指定的目录）：
    <片名>.mp4        视频
    <片名>.fr.srt     法语字幕
    <片名>.zh.srt     中文翻译字幕（独立）
    <片名>.bilingual.srt  中法双语字幕
    <片名>.fr.vtt / .zh.vtt / .bilingual.vtt
    <片名>.fr.txt     法语纯文本台词

依赖（install.bat 会装好）：
    yt-dlp, faster-whisper, argostranslate, ffmpeg（经 static-ffmpeg）

首次运行会自动下载模型（whisper large-v3-turbo ~1.5GB、argos 语言包 ~几百 MB），
之后离线可用。
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

__version__ = "1.1.1"
TRANSCRIPT_SCHEMA = 2   # 2 = 已处理音轨断口（见「时间轴对齐」一节）

# ----------------------------------------------------------------------------
# 路径与工具定位
# ----------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent

def _ffmpeg_dir():
    """定位 static-ffmpeg 提供的 ffmpeg/ffprobe 所在目录；找不到就返回 None（依赖系统 PATH）。"""
    try:
        import static_ffmpeg
        ffmpeg, ffprobe = static_ffmpeg.run.get_or_fetch_platform_executables_else_raise()
        return str(Path(ffmpeg).parent)
    except Exception:
        return None

FFMPEG_DIR = _ffmpeg_dir()


def run(cmd, **kw):
    """运行命令，打印实时输出，出错抛异常。"""
    print("  $ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


# ----------------------------------------------------------------------------
# 日志：同时写控制台与文件
# ----------------------------------------------------------------------------

LOG_PATH = None
_LOG_FH = None
_ORIG_OUT = sys.stdout
_ORIG_ERR = sys.stderr
DEBUG = False


class _Tee:
    """把写入同时转发到原始流和日志文件，用来兜住所有 print 与异常回溯。"""

    def __init__(self, stream, fh):
        self._stream = stream
        self._fh = fh

    def write(self, s):
        try:
            self._stream.write(s)
        except Exception:
            pass
        try:
            self._fh.write(s)
            self._fh.flush()
        except Exception:
            pass
        return len(s)

    def flush(self):
        for x in (self._stream, self._fh):
            try:
                x.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return self._stream.isatty()
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self._stream, name)


def setup_log(out_dir, debug=False):
    """把全部输出同时写进 <out>/.france2subs_work/france2subs.log，便于事后排查。"""
    global LOG_PATH, _LOG_FH, DEBUG
    DEBUG = debug
    try:
        work = Path(out_dir) / ".france2subs_work"
        work.mkdir(parents=True, exist_ok=True)
        LOG_PATH = work / "france2subs.log"
        _LOG_FH = open(LOG_PATH, "a", encoding="utf-8")
        sys.stdout = _Tee(_ORIG_OUT, _LOG_FH)
        sys.stderr = _Tee(_ORIG_ERR, _LOG_FH)
        log(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')}  france2subs {__version__} =====")
        log(f"  日志文件：{LOG_PATH}")
    except Exception:
        LOG_PATH = None
    return LOG_PATH


def log(msg, console=True):
    """写一行带时间戳的日志。console=False 时只进文件（用于 yt-dlp 的碎行）。"""
    line = str(msg)
    if console:
        try:
            _ORIG_OUT.write(line + "\n")
            _ORIG_OUT.flush()
        except Exception:
            pass
    if _LOG_FH:
        try:
            _LOG_FH.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
            _LOG_FH.flush()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 步骤 1：下载视频
# ----------------------------------------------------------------------------

def ensure_hls_decrypt():
    """检查 AES-128 解密库。

    france.tv 的 HLS 全是 AES-128 加密。没有 pycryptodomex 时 yt-dlp 只能把
    整条流交给外部 ffmpeg（日志里会出现 "extraction will be delegated to ffmpeg"），
    而 ffmpeg 单进程拉 HLS 没有分片级重试、没有断点续传，
    网络一抖就整条失败、只留下 .part-FragNNN.part 残片。
    """
    import importlib

    def ok():
        try:
            importlib.invalidate_caches()
            import Cryptodome  # noqa: F401
            return True
        except Exception:
            return False

    if ok():
        return True

    log("  未检测到 pycryptodomex，正在尝试自动安装 ...")
    cmds = [
        [sys.executable, "-m", "pip", "install", "-q", "pycryptodomex"],
        [sys.executable, "-m", "pip", "install", "-q", "pycryptodomex",
         "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
    ]
    for c in cmds:
        try:
            subprocess.run(c, check=True, timeout=240,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log(f"  安装尝试失败：{e}")
            continue
        if ok():
            log("  pycryptodomex 安装成功（启用分片重试与断点续传）")
            return True

    log("  [警告] 仍缺少 pycryptodomex，下载将由 ffmpeg 代劳：")
    log("         这种方式没有分片重试、没有断点续传，网络抖动时容易整体失败。")
    log("         修复：python -m pip install pycryptodomex")
    return False


class _YdlLogger:
    """把 yt-dlp 的日志接进我们的日志文件（进度另有 progress_hook，这里过滤掉刷屏行）。"""

    _NOISE = ("[debug] ", "[download] ")

    def debug(self, msg):
        s = str(msg).strip()
        if s.startswith(self._NOISE):
            return                      # 进度行交给 progress_hook，避免日志被刷爆
        log("[yt-dlp] " + s, console=DEBUG)

    def info(self, msg):
        log("[yt-dlp] " + str(msg).strip(), console=False)

    def warning(self, msg):
        log("  [yt-dlp 警告] " + str(msg).strip())

    def error(self, msg):
        log("  [yt-dlp 错误] " + str(msg).strip())


def _make_progress_hook():
    state = {"pct": -10.0, "t": 0.0}

    def hook(d):
        if d.get("status") != "downloading":
            return
        now = time.time()
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        pct = (done * 100.0 / total) if total else 0.0
        # 每 10% 或每 30 秒记一次，避免日志被刷爆
        if pct - state["pct"] < 10 and now - state["t"] < 30:
            return
        state["pct"], state["t"] = pct, now
        speed = d.get("speed") or 0
        eta = d.get("eta") or 0
        frag = ""
        if d.get("fragment_index") is not None:
            frag = f"  分片 {d.get('fragment_index')}/{d.get('fragment_count') or '?'}"
        log(f"  下载中 {pct:5.1f}%  {done/1048576:.0f}/{total/1048576:.0f} MB  "
            f"{speed/1048576:.2f} MB/s  剩 {eta/60:.0f} 分{frag}")

    return hook


def _probe_streams(path):
    """返回 (是否含视频流, 是否含音频流, 时长秒)；读不到返回 (False, False, None)。"""
    try:
        p = subprocess.run(
            [str(_ffprobe_bin()), "-v", "error",
             "-show_entries", "format=duration:stream=codec_type",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True)
        d = json.loads(p.stdout or "{}")
        kinds = [s.get("codec_type") for s in (d.get("streams") or [])]
        dur = (d.get("format") or {}).get("duration")
        dur = float(dur) if dur not in (None, "N/A") else None
        return ("video" in kinds, "audio" in kinds, dur)
    except Exception:
        return (False, False, None)


def verify_video(path, expect_min_seconds=60):
    """校验下载成品：文件够大、音视频流齐全、时长合理。"""
    path = Path(path)
    if not path.exists():
        return False, "文件不存在"
    mb = path.stat().st_size / 1048576
    if mb < 20:
        return False, f"文件过小（{mb:.1f} MB），应是没下完整"
    has_v, has_a, dur = _probe_streams(path)
    if not has_v:
        return False, "缺少视频流"
    if not has_a:
        return False, "缺少音频流（只下到视频轨，未完成合并）"
    if dur is not None and dur < expect_min_seconds:
        return False, f"时长仅 {dur:.0f} 秒，明显不完整"
    return True, f"{mb:.0f} MB / {dur:.0f}s" if dur else f"{mb:.0f} MB"


def cleanup_orphans(out_dir):
    """清掉下载成功后的残留分片/临时文件。"""
    n = 0
    for pat in ("*.part", "*.part-Frag*", "*.ytdl", "*.temp"):
        for f in Path(out_dir).glob(pat):
            try:
                f.unlink()
                n += 1
            except Exception:
                pass
    if n:
        log(f"  已清理 {n} 个残留临时文件")


def download_video(url, out_dir, max_height=1080, attempts=3):
    log("\n[1/4] 下载视频 ...")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    import yt_dlp

    has_decrypt = ensure_hls_decrypt()

    opts = {
        "outtmpl": str(out_dir / "%(title)s [%(id)s].%(ext)s"),
        "format": f"bestvideo[height<={max_height}]+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "logger": _YdlLogger(),
        "progress_hooks": [_make_progress_hook()],
        # ---- 抗网络抖动 ----
        "retries": 20,                      # 普通 HTTP 请求
        "fragment_retries": 30,             # 单个 HLS 分片（缺解密库时此项无效）
        "concurrent_fragment_downloads": 4 if has_decrypt else 1,
        "socket_timeout": 30,               # 默认 20 秒，放宽避免慢 CDN 被误杀
        "continuedl": True,                 # 断点续传，重试时不从头开始
        "overwrites": False,
        "nopart": False,                    # 保留 .part 才能在中断后接着下
        "hls_prefer_native": has_decrypt,
        "retry_sleep_functions": {
            "http": lambda n: min(2 ** n, 30),
            "fragment": lambda n: min(2 ** n, 30),
            "file_access": lambda n: 0,
        },
    }
    if DEBUG:
        opts["verbose"] = True
    if FFMPEG_DIR:
        opts["ffmpeg_location"] = FFMPEG_DIR

    last_err = None
    for i in range(1, attempts + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info and "entries" in info:
                    info = info["entries"][0]
                filename = ydl.prepare_filename(info)
            filename = re.sub(r"\.(webm|mkv|mp4|m4a)$", ".mp4", filename)

            path = Path(filename)
            if not path.exists():
                cands = [c for c in out_dir.glob(path.stem + ".*")
                         if c.suffix in (".mp4", ".mkv", ".webm")]
                if cands:
                    path = cands[0]

            good, why = verify_video(path)
            if good:
                log(f"  已下载: {path.name}  ({why})")
                cleanup_orphans(out_dir)
                return path
            last_err = why
            log(f"  [校验未通过] {why}")

        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            log(f"  [第 {i}/{attempts} 次下载失败] {last_err}")

        if i < attempts:
            log("  8 秒后重试（已下好的分片会复用，不会从头再来）...")
            time.sleep(8)

    raise RuntimeError(
        f"下载失败（已重试 {attempts} 次）：{last_err}\n"
        f"  详细日志：{LOG_PATH}")


# ----------------------------------------------------------------------------
# 步骤 2：转写（faster-whisper）
# ----------------------------------------------------------------------------

# ==== 时间轴对齐 ============================================================
# 背景：HLS 合流、录制中断、分段时间戳重置都会让音轨的 PTS 出现「跳空」，
# 例如本片在 13:52 处 PTS 从 831.979 直接跳到 840.000，中间 8 秒没有音频包。
# 如果直接 `ffmpeg -i video -f s16le -` 解码，输出的是「连续、无空洞」的 PCM，
# 那 8 秒会被压缩掉 —— 于是 14:00 之后的每一句字幕都提前 8 秒，越往后越错。
#
# 解决：解码时用 aresample=async=1 按容器 PTS 补齐静音，保证
#         PCM 时间轴 == 容器时间轴 == 播放器时间轴。
# 另外对旧版缓存（v1 未对齐）提供纯数学换算，避免用户白白重跑识别。
# ============================================================================

def _ffmpeg_bin():
    return (Path(FFMPEG_DIR) / "ffmpeg.exe") if FFMPEG_DIR else "ffmpeg"


def _ffprobe_bin():
    return (Path(FFMPEG_DIR) / "ffprobe.exe") if FFMPEG_DIR else "ffprobe"


def probe_media(video_path):
    """读取容器总时长与音轨时长（秒）。读不到就返回 None 字段。"""
    out = {"duration": None, "audio_duration": None}
    try:
        p = subprocess.run(
            [str(_ffprobe_bin()), "-v", "error",
             "-show_entries", "format=duration:stream=codec_type,duration",
             "-of", "json", str(video_path)],
            capture_output=True, text=True, check=True)
        d = json.loads(p.stdout or "{}")
        dur = (d.get("format") or {}).get("duration")
        if dur not in (None, "N/A"):
            out["duration"] = float(dur)
        for st in d.get("streams") or []:
            if st.get("codec_type") == "audio":
                a = st.get("duration")
                if a not in (None, "N/A"):
                    out["audio_duration"] = float(a)
                break
    except Exception as e:
        print(f"  [对齐] 读取容器信息失败（跳过检查）：{e}", flush=True)
    return out


def find_audio_gaps(video_path, min_missing=0.25):
    """找出音轨里 PTS 跳空的位置。

    返回 [(gap_start, gap_end, missing_seconds), ...]，时间为容器时间轴。
    """
    try:
        p = subprocess.run(
            [str(_ffprobe_bin()), "-v", "error", "-select_streams", "a:0",
             "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, check=True)
    except Exception:
        return []

    pts = []
    for line in p.stdout.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            pts.append(float(line))
        except ValueError:
            pass
    if len(pts) < 3:
        return []

    pts.sort()
    deltas = [b - a for a, b in zip(pts, pts[1:]) if b - a > 0]
    if not deltas:
        return []
    nominal = statistics.median(deltas)          # 正常包间隔（44.1k/48k 下约 0.021s）
    threshold = max(0.12, nominal * 3)

    gaps = []
    for a, b in zip(pts, pts[1:]):
        d = b - a
        if d > threshold:
            missing = d - nominal
            if missing >= min_missing:
                gaps.append((round(a, 3), round(b, 3), round(missing, 3)))
    return gaps


def remap_timeline(rows, gaps):
    """把「无空洞的压缩时间轴」上的时间戳换算到容器时间轴。

    用于修复旧版（v1）产生的转写缓存：那版解码没补静音，所有断口之后的时间戳
    都偏早了「累计缺失秒数」。这里按断口位置做分段平移，纯数学、不重跑识别。
    """
    if not gaps:
        return rows

    # 每个断口在「压缩轴」上的位置 = 容器位置 - 它之前已经压缩掉的秒数
    pivots = []
    cum = 0.0
    for gap_start, _gap_end, missing in sorted(gaps):
        pivots.append((gap_start - cum, cum + missing))
        cum += missing

    def conv(t):
        shift = 0.0
        for pos, after in pivots:
            if t >= pos:
                shift = after
            else:
                break
        return t + shift

    out = []
    for seg in rows:
        seg = dict(seg)
        seg["start"] = round(conv(seg["start"]), 3)
        seg["end"] = round(conv(seg["end"]), 3)
        seg["words"] = [dict(w, s=round(conv(w["s"]), 3), e=round(conv(w["e"]), 3))
                        for w in (seg.get("words") or [])]
        out.append(seg)
    return out


def _hms(t):
    """把秒格式化成 mm:ss.SSS，便于人看。"""
    t = max(0.0, float(t))
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:06.3f}"


def report_alignment(gaps, media):
    """打印对齐检查结果。"""
    if gaps:
        total = sum(g[2] for g in gaps)
        print(f"  检测到 {len(gaps)} 处音轨断口，共缺失 {total:.2f}s：", flush=True)
        for gs, ge, miss in gaps:
            print(f"    · {_hms(gs)} → {_hms(ge)}  缺 {miss:.2f}s", flush=True)
        print("    → 解码时会补静音，让 PCM 时间轴与容器/播放器时间轴一致", flush=True)
    else:
        print("  未发现音轨断口，时间轴正常", flush=True)
    if media.get("duration"):
        print(f"  容器时长 {_hms(media['duration'])}，"
              f"音轨 {_hms(media['audio_duration'] or 0)}", flush=True)


def extract_audio(video_path, out_path, sr=16000):
    """抽音轨为 16k 单声道 WAV。

    `aresample=async=1:first_pts=0` 会按 PTS 在跳空处补静音、并把起点强制对到 0，
    这是字幕与画面同步的关键。少了它，容器里的时间空洞会被压缩，
    导致断口之后的所有字幕提前（本片实测提前 8 秒）。
    """
    run([_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(video_path), "-vn",
         "-af", "aresample=async=1:min_hard_comp=0.005:first_pts=0",
         "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(out_path)])
    return out_path


def verify_audio_align(wav_path, media_duration, sr=16000):
    """校验 WAV 时长与容器时长是否一致（差值应为 0）。"""
    try:
        size = os.path.getsize(wav_path) - 44          # 去掉 WAV 头
        wav_dur = size / (2 * sr)
    except OSError:
        return None
    if not media_duration:
        return wav_dur
    diff = wav_dur - media_duration
    ok = "OK" if abs(diff) <= 0.5 else "WARN 时间轴可能仍有偏差"
    print(f"  校验：音频 {_hms(wav_dur)} / 容器 {_hms(media_duration)}，"
          f"差 {diff:+.2f}s  [{ok}]", flush=True)
    return diff


def transcribe(audio_path, model_name="large-v3-turbo", language="fr", cpu_threads=None):
    print("\n[2/4] 语音识别（法语） ...", flush=True)
    import numpy as np
    from faster_whisper import WhisperModel

    # 用 ffmpeg 解码为 float32，避免 faster-whisper 与新版 PyAV 的兼容问题
    proc = subprocess.run(
        [str(_ffmpeg_bin()), "-hide_banner", "-loglevel", "error",
         "-i", str(audio_path), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True, check=True)
    audio = np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0


    if cpu_threads is None:
        cpu_threads = max(1, (os.cpu_count() or 4) - 1)

    t0 = time.time()
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=cpu_threads)
    print(f"  模型加载完成（{time.time()-t0:.1f}s），开始转写 ...", flush=True)

    segments, info = model.transcribe(
        audio,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=200),
        condition_on_previous_text=False,
        word_timestamps=True,
        initial_prompt=("Émission de télévision française. "
                        "Cuisine, Auvergne, recettes, truites, pêche."),
    )

    rows = []
    for i, s in enumerate(segments):
        words = [{"w": w.word, "s": round(w.start, 3), "e": round(w.end, 3)}
                 for w in (s.words or [])]
        rows.append({"id": i, "start": round(s.start, 3), "end": round(s.end, 3),
                     "text": s.text.strip(), "words": words})
        if i % 25 == 0:
            el = time.time() - t0
            pos = rows[-1]["end"]
            print(f"  进度 {pos:6.0f}s / {len(audio)/16000:.0f}s  已用 {el/60:.1f} 分钟", flush=True)

    print(f"  完成，共 {len(rows)} 段（{time.time()-t0:.1f}s）", flush=True)
    return rows


# ----------------------------------------------------------------------------
# 步骤 3：把词级时间戳重排成字幕 cue
# ----------------------------------------------------------------------------

MAX_LINE = 42
MAX_CUE = 7.0
SOFT_MAX_CHARS = 80
MIN_CUE = 1.0
FLOOR_CUE = 0.7
GAP = 0.08
PAUSE_SPLIT = 0.55
CPS_TARGET = 22.0
PUNCT_END = ".!?…"
PUNCT_SOFT = ",;:"


def _clean(t):
    return re.sub(r"\s+", " ", t).strip().replace("’", "'")


def _wrap2(text, max_line=MAX_LINE):
    words = text.split()
    if not words:
        return [""]
    if len(text) <= max_line or len(words) == 1:
        return [text]

    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > max_line or len(b) > max_line:
            continue
        score = abs(len(a) - len(b))
        if i == 1 or i == len(words) - 1:
            score += 6
        if a[-1] in PUNCT_END:
            score -= 8
        elif a[-1] in PUNCT_SOFT:
            score -= 5
        if best is None or score < best[0]:
            best = (score, [a, b])
    if best:
        return best[1]

    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        ov = max(0, len(a) - max_line) + max(0, len(b) - max_line)
        score = ov + (6 if (i == 1 or i == len(words) - 1) else 0)
        if best is None or score < best[0]:
            best = (score, [a, b])
    return best[1] if best else [text]


def build_cues(segments):
    cues = []
    for seg in segments:
        words = seg.get("words") or []
        if not words:
            txt = _clean(seg["text"])
            if txt:
                cues.append([seg["start"], seg["end"], txt])
            continue

        cur = []

        def flush():
            nonlocal cur
            if not cur:
                return
            s, e = cur[0]["s"], cur[-1]["e"]
            while e - s > MAX_CUE and len(cur) > 1:
                cut = len(cur)
                for k in range(1, len(cur)):
                    if cur[k]["s"] - s >= MAX_CUE * 0.75:
                        cut = k
                        break
                head, cur = cur[:cut], cur[cut:]
                cues.append([head[0]["s"], head[-1]["e"], _clean("".join(w["w"] for w in head))])
                s, e = cur[0]["s"], cur[-1]["e"]
            txt = _clean("".join(w["w"] for w in cur))
            if txt:
                cues.append([s, e, txt])
            cur = []

        for w in words:
            if cur:
                gap = w["s"] - cur[-1]["e"]
                cand = _clean("".join(x["w"] for x in cur) + w["w"])
                dur = w["e"] - cur[0]["s"]
                need_break = False
                if len(cand) > SOFT_MAX_CHARS or dur > MAX_CUE:
                    need_break = True
                elif gap >= PAUSE_SPLIT and len(_clean("".join(x["w"] for x in cur))) >= 12:
                    need_break = True
                elif cur[-1]["w"].rstrip().endswith(tuple(PUNCT_END)) and len(cand) >= 25:
                    need_break = True
                if need_break:
                    flush()
            cur.append(w)
        flush()
    return cues


def finalize(cues):
    out = [[s, e, _clean(t)] for s, e, t in cues]
    out = [x for x in out if x[2] and re.search(r"\w", x[2])]
    out.sort(key=lambda x: x[0])

    n = len(out)
    fixed = []
    for i, (s, e, t) in enumerate(out):
        if fixed:
            prev_end = fixed[-1][1]
            if s < prev_end + GAP:
                s = prev_end + GAP
                if e < s + FLOOR_CUE:
                    e = s + FLOOR_CUE
        need = max(MIN_CUE, len(t) / CPS_TARGET)
        e = max(e, s + need)
        e = min(e, s + MAX_CUE)
        if i + 1 < n:
            limit = out[i + 1][0] - GAP
            if e > limit:
                e = max(s + FLOOR_CUE, min(e, limit))
        fixed.append([s, e, t])
    return fixed


def _ts(t, comma=True):
    if t < 0:
        t = 0
    h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s += 1; ms = 0
    return f"{h:02d}:{m:02d}:{s:02d}{',' if comma else '.'}{ms:03d}"


def _write_srt(cues, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, (s, e, t) in enumerate(cues, 1):
            f.write(f"{i}\n{_ts(s)} --> {_ts(e)}\n" + "\n".join(_wrap2(t)) + "\n\n")


def _write_vtt(cues, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for s, e, t in cues:
            f.write(f"{_ts(s, False)} --> {_ts(e, False)}\n" + "\n".join(_wrap2(t)) + "\n\n")


def _write_txt(cues, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(t for s, e, t in cues) + "\n")


# ----------------------------------------------------------------------------
# 步骤 4：翻译（Argos Translate，法→英→中，纯离线）
# ----------------------------------------------------------------------------

_AT = None  # 缓存的翻译器


def _get_translators():
    global _AT
    if _AT is None:
        # 分句方式决定是否会联网：
        #   argostranslate >= 1.11 用 ARGOS_CHUNK_TYPE，MINISBD = 纯本地小模型分句
        #   旧版用 ARGOS_STANZA_AVAILABLE=false 关掉 stanza（stanza 每次启动都会去
        #   raw.githubusercontent.com 拉 resources.json，弱网下必失败）
        os.environ["ARGOS_CHUNK_TYPE"] = "MINISBD"
        os.environ["ARGOS_STANZA_AVAILABLE"] = "false"
        try:
            import argostranslate.translate as t
            _AT = (t.get_translation_from_codes("fr", "en"),
                   t.get_translation_from_codes("en", "zh"))
        except Exception as e:
            msg = str(e)
            print("\n  [错误] 翻译模型加载失败。", flush=True)
            if "SSL" in msg or "urlopen" in msg or "onnx" in msg.lower():
                print("  原因：首次使用需要下载一个很小的分句模型（约 185KB），当前网络被拦。", flush=True)
                print("  处理：联网后重跑一次 install.bat 预热模型，或改用自己的网络重试本命令。", flush=True)
            print(f"  原始错误：{msg[:300]}", flush=True)
            raise
    return _AT


def _clean_zh(s):
    """清洗中文翻译里常见的模型噪声。"""
    # 去掉维基百科式的存档标记噪声
    s = re.sub(r"\(原始内容存档[^)]*\)", "", s)
    s = re.sub(r"\([^)]*archive[^)]*\)", "", s, flags=re.IGNORECASE)
    # 去掉残留的英文碎片（常见于地名直翻失败）
    s = re.sub(r"\bIt\b", "", s)
    # 收尾：去掉多余空白与孤立标点
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^[,，。、;；:：\s]+", "", s)
    s = re.sub(r"[,，。、;；:：\s]+$", "", s)
    # 去掉因清理而残留的孤立英文句点
    s = re.sub(r"^\s*\.\s*", "", s)
    s = re.sub(r"\s*\.\s*$", "", s)
    return s


def translate_cues(cues):
    print("\n[3/4] 翻译成中文（法→英→中，离线） ...", flush=True)
    fr2en, en2zh = _get_translators()
    out = []
    n = len(cues)
    for i, (s, e, fr) in enumerate(cues):
        mid = fr2en.translate(fr)
        zh = _clean_zh(en2zh.translate(mid))
        out.append([s, e, fr, zh])
        if i % 50 == 0:
            print(f"  进度 {i+1}/{n}", flush=True)
    print(f"  完成 {n} 条", flush=True)
    return out


def make_bilingual(cues):
    """把 fr + zh 合并成双语 cue。

    返回 [s, e, fr, zh] 四元组；写双语字幕时法语、中文各自折行后上下拼接。
    """
    return [[s, e, fr, zh] for s, e, fr, zh in cues]


def _write_bilingual_srt(bi_cues, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, (s, e, fr, zh) in enumerate(bi_cues, 1):
            body = "\n".join(_wrap2(fr) + _wrap2(zh))
            f.write(f"{i}\n{_ts(s)} --> {_ts(e)}\n{body}\n\n")


def _write_bilingual_vtt(bi_cues, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for s, e, fr, zh in bi_cues:
            body = "\n".join(_wrap2(fr) + _wrap2(zh))
            f.write(f"{_ts(s, False)} --> {_ts(e, False)}\n{body}\n\n")


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="下载 france.tv 视频并生成字幕")
    ap.add_argument("url", help="france.tv 视频页面 URL")
    ap.add_argument("--subtitle", choices=["fr", "zh", "bilingual", "all"],
                    default="bilingual", help="生成哪种字幕（默认 bilingual 中法双语）")
    ap.add_argument("--max-height", type=int, default=1080, help="视频最大高度")
    ap.add_argument("--out", default=".", help="输出目录")
    ap.add_argument("--model", default="large-v3-turbo", help="whisper 模型名")
    ap.add_argument("--skip-download", action="store_true", help="跳过下载（用 --out 里已有的 mp4）")
    ap.add_argument("--video", default=None, help="已有视频文件路径（配合 --skip-download）")
    ap.add_argument("--retranscribe", action="store_true", help="忽略转写缓存，重新识别")
    ap.add_argument("--debug", action="store_true", help="输出 yt-dlp 详细调试日志")
    ap.add_argument("--version", action="version", version=f"france2subs {__version__}")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_log(out_dir, args.debug)

    # 1. 下载
    if args.skip_download:
        if not args.video:
            cands = list(out_dir.glob("*.mp4"))
            if not cands:
                print("未找到视频文件，请用 --video 指定", file=sys.stderr)
                return 1
            video = Path(cands[0])
        else:
            video = Path(args.video)
    else:
        try:
            video = download_video(args.url, out_dir, args.max_height)
        except RuntimeError as e:
            log(f"\n[失败] {e}")
            log("  常见原因：网络中断、站点限流、视频有地区限制（需法国 IP）。")
            log("  重新运行同一条命令即可续传，已下载的分片不会浪费。")
            return 1

    base = video.stem  # 去掉扩展名的文件名

    # === 时间轴对齐检查（决定字幕能否与画面同步）===
    print("\n[对齐] 检查音轨时间轴 ...", flush=True)
    media = probe_media(video)
    gaps = find_audio_gaps(video)
    report_alignment(gaps, media)

    # 2. 转写（有缓存则复用）
    work = out_dir / ".france2subs_work"
    work.mkdir(exist_ok=True)
    transcript_file = work / (base + ".transcript.json")

    cache = None
    if transcript_file.exists() and not args.retranscribe:
        try:
            cache = json.loads(transcript_file.read_text(encoding="utf-8"))
        except Exception:
            cache = None

    def _save_cache(rows, extra_note=""):
        transcript_file.write_text(
            json.dumps({"schema": TRANSCRIPT_SCHEMA,
                        "duration": media.get("duration"),
                        "audio_gaps": gaps,
                        "segments": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        if extra_note:
            print(f"  {extra_note}", flush=True)

    if cache is not None and cache.get("schema") == TRANSCRIPT_SCHEMA:
        print(f"\n  复用已有转写缓存（时间轴已对齐）: {transcript_file}", flush=True)
        rows = cache["segments"]
    elif cache is not None:
        # 旧版缓存：v1 抽音频时没补静音，断口之后的时间戳整体偏早
        if gaps:
            print(f"\n  旧版转写缓存未处理音轨断口，正在按 {len(gaps)} 处断口修正时间轴"
                  f"（无需重新识别，省 {len(cache.get('segments') or [])} 段识别耗时）", flush=True)
            rows = remap_timeline(cache["segments"], gaps)
        else:
            print("\n  复用旧版转写缓存（无音轨断口，时间轴无需修正）", flush=True)
            rows = cache["segments"]
        _save_cache(rows, "缓存已升级到 schema 2")
    else:
        audio = work / (base + ".wav")
        print("\n  抽取音轨 ...", flush=True)
        extract_audio(video, audio)
        verify_audio_align(audio, media.get("duration"))
        rows = transcribe(audio, args.model)
        _save_cache(rows)

    # 时间轴健全性检查：最后一句不应超出视频长度
    if rows and media.get("duration"):
        last = max(r["end"] for r in rows)
        mark = "OK" if last <= media["duration"] + 1.0 else "WARN 超出视频长度"
        print(f"  [对齐] 识别结果覆盖到 {_hms(last)} / 视频 {_hms(media['duration'])}  [{mark}]",
              flush=True)


    # 3. 排版
    cues = finalize(build_cues(rows))

    # 4. 写出法语字幕
    fr_srt = out_dir / (base + ".fr.srt")
    fr_vtt = out_dir / (base + ".fr.vtt")
    fr_txt = out_dir / (base + ".fr.txt")
    _write_srt(cues, fr_srt)
    _write_vtt(cues, fr_vtt)
    _write_txt(cues, fr_txt)
    print(f"\n  法语字幕已写出: {fr_srt}", flush=True)

    # 5. 翻译 + 双语
    if args.subtitle in ("zh", "bilingual", "all"):
        translated = translate_cues(cues)

        zh_cues = [[s, e, zh] for s, e, fr, zh in translated]
        zh_srt = out_dir / (base + ".zh.srt")
        zh_vtt = out_dir / (base + ".zh.vtt")
        _write_srt(zh_cues, zh_srt)
        _write_vtt(zh_cues, zh_vtt)
        print(f"  中文字幕已写出: {zh_srt}", flush=True)

        bi_cues = make_bilingual(translated)
        bi_srt = out_dir / (base + ".bilingual.srt")
        bi_vtt = out_dir / (base + ".bilingual.vtt")
        _write_bilingual_srt(bi_cues, bi_srt)
        _write_bilingual_vtt(bi_cues, bi_vtt)
        print(f"  双语字幕已写出: {bi_srt}", flush=True)

    print("\n[4/4] 全部完成！", flush=True)
    for p in sorted(out_dir.glob(base + ".*")):
        print(f"  · {p}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
