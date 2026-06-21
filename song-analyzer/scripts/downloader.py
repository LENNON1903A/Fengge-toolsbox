#!/usr/bin/env python3
"""
音频下载与预处理工具 — Audio Downloader & Preprocessor
=====================================================
处理多平台音频获取：网络链接 → 本地音频文件。

支持平台：
  - YouTube, SoundCloud, Bandcamp (yt-dlp 直接支持)
  - B站 (bilibili), 网易云音乐, QQ音乐 (yt-dlp)
  - 今日头条 (yt-dlp + 移动端 API 回退)
  - 抖音 (yt-dlp douyin extractor)
  - 微信视频号 (需浏览器自动化回退)

输出：统一转换为 WAV 格式供分析器使用。
依赖：yt-dlp, ffmpeg
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse


# ============================================================
# ffmpeg 路径检测
# ============================================================

def _find_ffmpeg_bin():
    """在 Windows 上自动查找 ffmpeg 安装路径。"""
    search_dirs = []
    winget_base = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                "Microsoft", "WinGet", "Packages")
    if os.path.isdir(winget_base):
        for d in os.listdir(winget_base):
            if "Gyan.FFmpeg" in d or "ffmpeg" in d.lower():
                for root, dirs, _ in os.walk(os.path.join(winget_base, d)):
                    for sub in dirs:
                        if sub == "bin":
                            search_dirs.append(os.path.join(root, sub))
    search_dirs.extend([
        r"C:\ffmpeg\bin", r"D:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin", r"D:\Programs\ffmpeg\bin",
    ])
    for d in search_dirs:
        if os.path.isfile(os.path.join(d, "ffmpeg.exe")):
            return d
    return ""

FFMPEG_BIN = _find_ffmpeg_bin()
FFMPEG = os.path.join(FFMPEG_BIN, "ffmpeg.exe") if FFMPEG_BIN else "ffmpeg"


# ============================================================
# 平台识别
# ============================================================

PLATFORM_PATTERNS = {
    "youtube": ["youtube.com", "youtu.be"],
    "bilibili": ["bilibili.com", "b23.tv"],
    "soundcloud": ["soundcloud.com"],
    "netease": ["music.163.com"],
    "qqmusic": ["y.qq.com", "i.y.qq.com"],
    "toutiao": ["toutiao.com", "ixigua.com", "xigua.com"],
    "douyin": ["douyin.com", "iesdouyin.com"],
    "wechat_video": ["channels.weixin.qq.com", "finder.video.qq.com"],
    "bing_video": ["bing.com/videos", "cn.bing.com/videos"],
}

PLATFORM_FRIENDLY = {
    "youtube": "YouTube",
    "bilibili": "B站",
    "soundcloud": "SoundCloud",
    "netease": "网易云音乐",
    "qqmusic": "QQ音乐",
    "toutiao": "今日头条/西瓜视频",
    "douyin": "抖音",
    "wechat_video": "微信视频号",
    "bing_video": "Bing视频",
}


def detect_platform(url: str) -> str:
    """识别 URL 所属平台。"""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pat in patterns:
            if pat in domain:
                return platform
    # 检查短链接域名
    if "m.toutiao.com" in domain:
        return "toutiao"
    return "unknown"


# ============================================================
# 下载策略
# ============================================================

def download_with_ytdlp(url: str, output_dir: str, platform: str = "unknown") -> str:
    """
    使用 yt-dlp 下载最佳音频。

    Returns:
        下载后的音频文件路径
    """
    output_template = os.path.join(output_dir, "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-check-certificates",
        "-x",                          # 仅提取音频
        "--audio-format", "wav",       # 输出 WAV
        "--audio-quality", "0",        # 最佳音质
        "-o", output_template,
        "--no-playlist",
        "--socket-timeout", "30",
        url
    ]

    # 平台特定 User-Agent
    env = os.environ.copy()
    if platform == "douyin":
        env["HTTP_USER_AGENT"] = (
            "Mozilla/5.0 (Linux; Android 13; SM-G9980) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        )
    elif platform == "toutiao":
        env["HTTP_USER_AGENT"] = (
            "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        )

    print(f"  [下载] 使用 yt-dlp 获取 {PLATFORM_FRIENDLY.get(platform, platform)} 音频...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env, cwd=output_dir,
                             encoding="utf-8", errors="replace")

    if result.returncode != 0:
        stderr = result.stderr[-500:] if len(result.stderr) > 500 else result.stderr
        print(f"  [WARN] yt-dlp 失败: {stderr}", file=sys.stderr)
        return ""

    # 解析输出找文件路径
    stdout = result.stdout
    # yt-dlp 在 stdout 输出文件路径
    for line in stdout.split("\n"):
        if "[ExtractAudio]" in line and "Destination:" in line:
            path = line.split("Destination:")[-1].strip()
            if os.path.exists(path):
                return path

    # 回退：搜索输出目录下最新 WAV
    wav_files = sorted(
        Path(output_dir).glob("*.wav"),
        key=lambda p: p.stat().st_mtime, reverse=True
    )
    for f in wav_files:
        if f.stat().st_size > 10000:  # 至少 10KB
            return str(f)

    return ""


def download_toutiao_fallback(url: str, output_dir: str) -> str:
    """
    头条/西瓜视频移动端 API 回退方案。
    参考「风哥头条视频下载」skill。
    """
    # 提取视频 ID
    vid_match = re.search(r'/(?:video|a)/(\d+)', url)
    if not vid_match:
        vid_match = re.search(r'i(\d+)', url)
    if not vid_match:
        return ""
    video_id = vid_match.group(1)

    print(f"  [回退] 尝试头条移动端 API (video_id={video_id})...")

    api_url = f"https://m.toutiao.com/i{video_id}/info/"
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "15",
             "-H", "User-Agent: Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36",
             api_url],
            capture_output=True, text=True, timeout=20
        )
        data = json.loads(result.stdout)
        video_url = ""
        if "data" in data:
            video_info = data["data"].get("video_list", {})
            # 选最高画质
            for quality in ["1080p", "720p", "480p", "360p"]:
                if quality in video_info:
                    video_url = video_info[quality].get("main_url", "")
                    break
        if video_url:
            return download_with_ytdlp(video_url, output_dir)
    except Exception as e:
        print(f"  [WARN] 头条 API 回退失败: {e}", file=sys.stderr)
    return ""


# ============================================================
# 文件预处理
# ============================================================

def extract_audio_from_video(filepath: str, output_dir: str) -> str:
    """
    从视频文件中提取音频为 WAV。

    Returns:
        提取后的 WAV 文件路径
    """
    stem = Path(filepath).stem
    output_path = os.path.join(output_dir, f"{stem}_extracted.wav")

    # 如果已存在同名提取文件，跳过
    if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
        print(f"  [跳过] 已存在提取文件: {output_path}")
        return output_path

    cmd = [
        FFMPEG, "-y",
        "-i", filepath,
        "-vn",                     # 去掉视频流
        "-acodec", "pcm_s16le",    # WAV PCM 16-bit
        "-ar", "44100",            # 44100 Hz
        "-ac", "2",                # 立体声
        output_path
    ]
    print(f"  [提取] 从视频提取音频: {os.path.basename(filepath)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                             encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(f"  [WARN] ffmpeg 提取音频失败: {result.stderr[-300:]}", file=sys.stderr)
        return ""
    return output_path


def convert_to_wav(filepath: str, output_dir: str) -> str:
    """
    将非 WAV 音频文件转换为 WAV 格式。
    """
    ext = Path(filepath).suffix.lower()
    if ext == ".wav" and os.path.getsize(filepath) > 10000:
        return filepath

    stem = Path(filepath).stem
    output_path = os.path.join(output_dir, f"{stem}_converted.wav")

    if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
        return output_path

    cmd = [
        FFMPEG, "-y",
        "-i", filepath,
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "2",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                             encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(f"  [WARN] 音频格式转换失败: {result.stderr[-300:]}", file=sys.stderr)
        return ""
    return output_path


# ============================================================
# 主入口
# ============================================================

def is_url(path: str) -> bool:
    return path.startswith(("http://", "https://", "www."))


def is_video_file(path: str) -> bool:
    video_exts = {".mp4", ".mkv", ".webm", ".flv", ".avi", ".mov", ".ts", ".m4v"}
    return Path(path).suffix.lower() in video_exts


def is_audio_file(path: str) -> bool:
    """检查是否是直接可用的音频文件。"""
    audio_exts = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".wma"}
    return Path(path).suffix.lower() in audio_exts


def process_input(input_path: str, output_dir: str = None) -> dict:
    """
    统一输入处理入口。

    Returns:
        {
            "audio_file": str,        # 最终 WAV 文件路径
            "original_input": str,    # 原始输入
            "platform": str,          # 平台名称
            "method": str,            # 处理方法
            "success": bool,
            "error": str,
        }
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="song_analysis_")

    os.makedirs(output_dir, exist_ok=True)

    result = {
        "audio_file": "",
        "original_input": input_path,
        "platform": "",
        "method": "",
        "success": False,
        "error": "",
    }

    # ---- Case 1: URL ----
    if is_url(input_path):
        platform = detect_platform(input_path)
        result["platform"] = platform

        if platform == "wechat_video":
            result["error"] = (
                "微信视频号暂不支持自动下载。请手动下载视频后提供本地文件路径。"
            )
            return result

        if platform == "bing_video":
            result["error"] = (
                "Bing视频搜索页是聚合页面，不包含可直接下载的视频源。"
                "请在Bing视频页面点击跳转到原始视频来源（YouTube/B站/抖音等），"
                "然后提供原始视频链接。"
            )
            return result

        # 尝试 yt-dlp
        audio_file = download_with_ytdlp(input_path, output_dir, platform)
        if audio_file:
            result["audio_file"] = audio_file
            result["method"] = "yt-dlp"
            result["success"] = True
            return result

        # 头条回退
        if platform == "toutiao":
            audio_file = download_toutiao_fallback(input_path, output_dir)
            if audio_file:
                result["audio_file"] = audio_file
                result["method"] = "toutiao-mobile-api"
                result["success"] = True
                return result

        result["error"] = f"所有下载方式均失败，请检查链接是否有效或手动下载后提供本地文件"
        return result

    # ---- Case 2: 本地视频文件 ----
    if is_video_file(input_path):
        if not os.path.exists(input_path):
            result["error"] = f"文件不存在: {input_path}"
            return result
        audio_file = extract_audio_from_video(input_path, output_dir)
        if audio_file:
            result["audio_file"] = audio_file
            result["method"] = "ffmpeg-extract"
            result["success"] = True
        else:
            result["error"] = "音频提取失败"
        return result

    # ---- Case 3: 本地音频文件 ----
    if is_audio_file(input_path):
        if not os.path.exists(input_path):
            result["error"] = f"文件不存在: {input_path}"
            return result
        # 如果已是 WAV 直接使用，否则转换
        if Path(input_path).suffix.lower() == ".wav":
            result["audio_file"] = input_path
            result["method"] = "direct"
            result["success"] = True
        else:
            audio_file = convert_to_wav(input_path, output_dir)
            if audio_file:
                result["audio_file"] = audio_file
                result["method"] = "ffmpeg-convert"
                result["success"] = True
            else:
                result["error"] = "音频格式转换失败"
        return result

    # ---- Case 4: 未知格式 ----
    result["error"] = f"不支持的输入格式: {input_path}\n支持: URL 链接 / MP4 / MP3 / WAV / FLAC / M4A / OGG / MKV / WEBM"
    return result


def main():
    parser = argparse.ArgumentParser(
        description="多平台音频下载与预处理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s "https://www.youtube.com/watch?v=xxx"
  %(prog)s "https://music.163.com/song?id=xxx"
  %(prog)s "C:/path/to/song.mp4"
  %(prog)s -o "D:/desktop/audio_temp" "https://www.bilibili.com/video/xxx"
        """,
    )
    parser.add_argument("input", help="URL 链接或本地文件路径")
    parser.add_argument("-o", "--output-dir", default=None,
                        help="输出目录 (默认: 系统临时目录)")
    args = parser.parse_args()

    result = process_input(args.input, args.output_dir)

    # 输出 JSON 结果供解析
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result["success"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
