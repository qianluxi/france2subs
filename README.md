# france2subs

下载 france.tv 视频，并自动生成**法语 / 中文 / 中法双语**字幕的小工具。

面向普通用户：粘贴网址 → 点一下 → 得到视频 + 字幕，全程本地运行、免费、无广告。

## 功能

- 下载 france.tv 视频（最高 1080p，自动合并音视频）
- 法语语音识别（本地 Whisper large-v3-turbo 模型）
- 中文翻译（本地 Argos 模型，法→英→中，纯离线）
- 输出多种字幕：
  - `<片名>.fr.srt` / `.fr.vtt` / `.fr.txt` — 法语
  - `<片名>.zh.srt` / `.zh.vtt` — 中文
  - `<片名>.bilingual.srt` / `.bilingual.vtt` — 中法双语
- 字幕与视频同名同目录，用 VLC / PotPlayer / MPV 打开视频即可自动加载

## 安装（只需一次）

1. 安装 Python 3.9 或更高版本（安装时勾选 **Add Python to PATH**）
   - 下载：https://www.python.org/downloads/
2. 双击运行 `install.bat`
   - 它会自动安装依赖库、准备 ffmpeg、预下载翻译模型

> 首次转写视频时会自动下载语音识别模型（约 1.5 GB），只需下载一次，之后离线可用。

## 使用

### 图形界面（推荐）

```
python app.py
```

粘贴 france.tv 网址 → 选字幕类型和画质 → 点「开始」→ 等进度条走完。

### 命令行

```
python france2subs.py "https://www.france.tv/..."
```

常用参数：

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--subtitle` | `fr` / `zh` / `bilingual` / `all` | `bilingual` |
| `--max-height` | 视频最大高度（1080/720/540/360） | `1080` |
| `--out` | 输出目录 | 当前目录 |
| `--model` | 语音模型名 | `large-v3-turbo` |
| `--skip-download` | 跳过下载，只给已有 mp4 配字幕 | 关闭 |
| `--video` | 已有视频文件路径（配合 `--skip-download`） | — |

示例：只给已经下好的视频配双语字幕

```
python france2subs.py "任意占位" --skip-download --video "我的视频.mp4" --subtitle bilingual
```

## 常见问题

**Q：转写很慢？**
正常。语音识别在本机 CPU 上跑，26 分钟的视频约需 5~10 分钟（视 CPU 而定）。

**Q：中文翻译有些词不对？**
翻译用本地离线模型，对地名、人名、方言的翻译可能有误差，属于离线方案的正常水平。法语字幕本身（语音识别）通常更准确，可优先看。

**Q：提示找不到 ffmpeg？**
重新运行 `install.bat`；它已把 ffmpeg 备好，脚本会自动定位。

**Q：下载报错？**
france.tv 的部分内容有地区限制（仅法国 IP 可看）。若下载失败，通常是该视频在你的网络区域不可用。

## 文件结构

```
france2subs/
├── france2subs.py   核心引擎（下载/转写/排版/翻译）
├── app.py           图形界面
├── install.bat      一键安装脚本
└── README.md        本文件
```
