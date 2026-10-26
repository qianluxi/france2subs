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
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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
# 步骤 1：下载视频
# ----------------------------------------------------------------------------

def download_video(url, out_dir, max_height=1080):
    print("\n[1/4] 下载视频 ...", flush=True)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    import yt_dlp

    opts = {
        "outtmpl": str(out_dir / "%(title)s [%(id)s].%(ext)s"),
        "format": f"bestvideo[height<={max_height}]+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
    }
    if FFMPEG_DIR:
        opts["ffmpeg_location"] = FFMPEG_DIR

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info:
            info = info["entries"][0]
        filename = ydl.prepare_filename(info)
        # 合并后扩展名统一为 .mp4
        filename = re.sub(r"\.(webm|mkv|mp4|m4a)$", ".mp4", filename)

    path = Path(filename)
    if not path.exists():
        # 某些情况文件名带格式后缀，做一次兜底查找
        cands = list(out_dir.glob(path.stem + ".*"))
        if cands:
            path = cands[0]
    print(f"  已下载: {path}", flush=True)
    return path


# ----------------------------------------------------------------------------
# 步骤 2：转写（faster-whisper）
# ----------------------------------------------------------------------------

def extract_audio(video_path, out_path, sr=16000):
    ffmpeg = (Path(FFMPEG_DIR) / "ffmpeg.exe") if FFMPEG_DIR else "ffmpeg"
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(video_path), "-vn", "-ac", "1", "-ar", str(sr),
         "-c:a", "pcm_s16le", str(out_path)])
    return out_path


def transcribe(audio_path, model_name="large-v3-turbo", language="fr", cpu_threads=None):
    print("\n[2/4] 语音识别（法语） ...", flush=True)
    import numpy as np
    from faster_whisper import WhisperModel

    ffmpeg = (Path(FFMPEG_DIR) / "ffmpeg.exe") if FFMPEG_DIR else "ffmpeg"
    # 用 ffmpeg 解码为 float32，避免 faster-whisper 与新版 PyAV 的兼容问题
    proc = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error",
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
        os.environ["ARGOS_STANZA_AVAILABLE"] = "false"  # 禁用 stanza，纯离线
        import argostranslate.translate as t
        _AT = (t.get_translation_from_codes("fr", "en"),
               t.get_translation_from_codes("en", "zh"))
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
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

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
        video = download_video(args.url, out_dir, args.max_height)

    base = video.stem  # 去掉扩展名的文件名

    # 2. 转写（有缓存则复用）
    work = out_dir / ".france2subs_work"
    work.mkdir(exist_ok=True)
    transcript_file = work / (base + ".transcript.json")

    if transcript_file.exists():
        print(f"\n  复用已有转写缓存: {transcript_file}", flush=True)
        rows = json.loads(transcript_file.read_text(encoding="utf-8"))["segments"]
    else:
        audio = work / (base + ".wav")
        extract_audio(video, audio)
        rows = transcribe(audio, args.model)
        transcript_file.write_text(
            json.dumps({"duration": None, "segments": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8")

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
