# -*- coding: utf-8 -*-
"""把 france.tv 官方 WEBVTT 字幕转成规范 SRT（去掉 <c.xxx> 配色标签、丢弃纯省略号/台标块）。

用法：
    python official_vtt_to_srt.py <in.vtt> <out.srt>
"""
import re
import sys

MAX_LINE = 42
MAX_LINES = 2
MAX_CUE = 7.0
MIN_CUE = 1.0

TAG_RE = re.compile(r"</?c(\.[a-zA-Z]+)?>|<[^>]+>")


def sec(ts):
    h, m, rest = ts.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def fmt(t, comma=True):
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def wrap(text, max_line=MAX_LINE):
    """折成最多两行，尽量均衡并优先在标点后断开。"""
    words = text.split()
    if len(text) <= max_line or len(words) < 2:
        return [text]
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > max_line or len(b) > max_line:
            continue
        bonus = 10 if a[-1] in ",.;:!?" else 0
        score = abs(len(a) - len(b)) - bonus
        if best is None or score < best[0]:
            best = (score, [a, b])
    if best:
        return best[1]
    # 实在放不下就硬切
    out, cur = [], ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= max_line:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out[:MAX_LINES] if len(out) <= MAX_LINES else out


def parse_vtt(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in raw.strip().split("\n\n"):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        if not lines:
            continue
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None or idx + 1 > len(lines):
            continue
        try:
            a, b = lines[idx].split("-->")
            start, end = sec(a.strip().split()[0]), sec(b.strip().split()[0])
        except Exception:
            continue
        body = " ".join(lines[idx + 1:])
        text = TAG_RE.sub("", body)
        text = re.sub(r"^[\s\-–—]+", "", text.strip())
        text = re.sub(r"\s+", " ", text).strip()
        cues.append([start, end, text])
    return cues


def clean(cues):
    out = []
    for s, e, t in cues:
        if not t:
            continue
        if re.fullmatch(r"[.\s…]+", t):          # 纯省略号（音乐/无声提示）
            continue
        if re.fullmatch(r"france\.?\s*tv(\s*access)?", t, re.I):   # 台标
            continue
        if not re.search(r"[0-9A-Za-zÀ-ÿ]", t):
            continue
        dur = e - s
        if dur < MIN_CUE:
            e = s + MIN_CUE
        if dur > MAX_CUE:
            e = s + MAX_CUE
        out.append([s, e, t])
    return out


def write_srt(cues, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, (s, e, t) in enumerate(cues, 1):
            f.write(f"{i}\n{fmt(s)} --> {fmt(e)}\n" + "\n".join(wrap(t)) + "\n\n")


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    cues = clean(parse_vtt(src))
    write_srt(cues, dst)
    total = sum(e - s for s, e, t in cues)
    print(f"cues={len(cues)}  时长={total/60:.1f}min  字数={sum(len(t) for _, _, t in cues)}")
