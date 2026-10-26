# -*- coding: utf-8 -*-
"""安装预热脚本：把「翻译语言包 + 分句模型 + 语音识别模型」提前下载好。

装好之后 france2subs 就可以完全离线运行，不会在正式使用时卡在下载上。
用法：
    python install_warmup.py            # 全部预热
    python install_warmup.py --no-whisper   # 跳过大模型（约 1.5GB）
"""
import os
import sys

# 这两个开关决定 argostranslate 是否联网（必须在 import 之前设置）
os.environ["ARGOS_CHUNK_TYPE"] = "MINISBD"        # 纯本地分句，替代 stanza
os.environ["ARGOS_STANZA_AVAILABLE"] = "false"    # 兼容旧版 argostranslate

WANT_PAIRS = [("fr", "en"), ("en", "zh")]


def log(msg):
    print(msg, flush=True)


def install_translation_packages():
    """下载并安装 fr->en、en->zh 语言包。"""
    log("[1/3] 翻译语言包 ...")
    try:
        import argostranslate.package as pkg
    except ImportError:
        log("  [错误] 未安装 argostranslate，请先运行 pip install -r requirements.txt")
        return False

    try:
        installed = {(p.from_code, p.to_code) for p in pkg.get_installed_packages()}
    except Exception:
        installed = set()

    try:
        available = pkg.get_available_packages()
    except Exception as e:
        log(f"  [警告] 无法读取语言包索引（网络问题）：{e}")
        log("         已有本地语言包仍可正常使用。")
        return bool(installed & set(WANT_PAIRS))

    ok = True
    for src, dst in WANT_PAIRS:
        if (src, dst) in installed:
            log(f"  [已装] {src} -> {dst}")
            continue
        cand = [p for p in available if p.from_code == src and p.to_code == dst]
        if not cand:
            log(f"  [警告] 索引中找不到 {src} -> {dst}")
            ok = False
            continue
        try:
            path = cand[0].download()
            pkg.install_from_path(path)
            log(f"  [完成] {src} -> {dst}")
        except Exception as e:
            log(f"  [警告] {src} -> {dst} 安装失败：{e}")
            ok = False
    return ok


def warmup_sbd():
    """触发 MiniSBD 分句模型的下载（每个语言约 185KB）。"""
    log("\n[2/3] 分句模型（MiniSBD，离线必需）...")
    try:
        import argostranslate.translate as tr
        fr2en = tr.get_translation_from_codes("fr", "en")
        en2zh = tr.get_translation_from_codes("en", "zh")
        fr2en.translate("Bonjour tout le monde.")
        en2zh.translate("Hello everybody.")
        log("  [完成] 分句模型就绪")
        return True
    except Exception as e:
        log(f"  [警告] 分句模型预热失败：{str(e)[:200]}")
        log("         正式使用时联网重试，或再跑一次本脚本。")
        return False


def warmup_whisper(model_name="large-v3-turbo"):
    """下载语音识别模型（首次约 1.5GB）。"""
    log(f"\n[3/3] 语音识别模型 {model_name}（约 1.5GB，首次较慢）...")
    try:
        from faster_whisper import WhisperModel
        WhisperModel(model_name, device="cpu", compute_type="int8")
        log("  [完成] 模型已缓存，之后可离线使用")
        return True
    except Exception as e:
        log(f"  [警告] 模型下载失败：{str(e)[:200]}")
        log("         首次正式运行时会再自动下载一次。")
        return False


def main(argv):
    skip_whisper = "--no-whisper" in argv
    log("=" * 46)
    log("  france2subs 预下载")
    log("=" * 46)
    install_translation_packages()
    warmup_sbd()
    if skip_whisper:
        log("\n[3/3] 已跳过语音识别模型（--no-whisper）")
    else:
        warmup_whisper()
    log("\n预热结束。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
