#!/usr/bin/env python3
"""
歌曲/音乐全面分析工具 — Song Comprehensive Analyzer
===================================================
对音频文件进行深度分析，提取全部音乐要素，输出 HTML 可视化报告和 Suno AI 风格提示词。

支持输入：MP3、MP4、WAV、FLAC、M4A、OGG、WebM 等常见音频/视频格式。
依赖：librosa, numpy, scipy, soundfile, pydub
系统工具：ffprobe (ffmpeg 套件)

分析维度（共 13 维，报告展示 12 维，曲式结构仅用于 Suno 提示词）：
  1. 基础信息  — 格式、时长、采样率、比特率、声道数
  2. BPM/速度  — 每分钟节拍数 + 速度风格分类
  3. 调式       — 调性(C/D/E...) + 大/小调 + 置信度
  4. 节奏特征  — 复杂度、切分音、摇摆感
  5. 频谱特征  — 质心/带宽/滚降/对比度 → 音色描述
  6. 能量动态  — RMS曲线 + 动态范围 + 压缩感
  7. 乐器推测  — HPSS分离 + 频段分析 → 乐器清单
  8. 人声特征  — pYIN基频 + MFCC → 性别/风格
  [9. 曲式结构  — 自相似矩阵段落分割（仅用于Suno提示词，不在报告中展示）]
  9. 情绪氛围 — BPM×调式×能量×频谱 → 情绪分类
  10. 和声走向 — 色度图模板匹配 → 和弦检测 + 常见进行识别
  11. 制作特征 — 混响感/立体声宽度/制作年代感
  12. 曲风归类 — 26类规则引擎推理 → 校园风/阿拉伯风/日本风/中国风等
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")


# ============================================================
# ffmpeg/ffprobe 路径检测
# ============================================================

def _find_ffmpeg_bin():
    """在 Windows 上自动查找 ffmpeg 安装路径。"""
    # 常见安装位置
    search_dirs = []
    # winget Gyan.FFmpeg 安装位置 (搜索模式)
    winget_base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    if os.path.isdir(winget_base):
        for d in os.listdir(winget_base):
            if "Gyan.FFmpeg" in d or "ffmpeg" in d.lower():
                for root, dirs, _ in os.walk(os.path.join(winget_base, d)):
                    for sub in dirs:
                        if sub == "bin":
                            search_dirs.append(os.path.join(root, sub))
    # 常见手动安装位置
    search_dirs.extend([
        r"C:\ffmpeg\bin",
        r"D:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin",
        r"D:\Programs\ffmpeg\bin",
    ])
    for d in search_dirs:
        ffprobe_path = os.path.join(d, "ffprobe.exe")
        if os.path.isfile(ffprobe_path):
            return d
    return ""

FFMPEG_BIN = _find_ffmpeg_bin()
FFPROBE = os.path.join(FFMPEG_BIN, "ffprobe.exe") if FFMPEG_BIN else "ffprobe"
FFMPEG = os.path.join(FFMPEG_BIN, "ffmpeg.exe") if FFMPEG_BIN else "ffmpeg"

# ============================================================
# 常量与映射表
# ============================================================

NOTE_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Schmuckler 调性模板
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

BPM_TO_TEMPO = [
    (0, 40, "Larghissimo"), (40, 60, "Largo"), (60, 66, "Larghetto"),
    (66, 76, "Adagio"), (76, 108, "Andante"), (108, 120, "Moderato"),
    (120, 140, "Allegro"), (140, 168, "Vivace"), (168, 200, "Presto"),
    (200, 999, "Prestissimo"),
]

BPM_TEMPO_CN = {
    "Larghissimo": "极慢板", "Largo": "广板", "Larghetto": "小广板",
    "Adagio": "柔板", "Andante": "行板", "Moderato": "中板",
    "Allegro": "快板", "Vivace": "活泼的快板", "Presto": "急板",
    "Prestissimo": "最急板",
}


def centroid_to_brightness(val):
    if val < 1500: return "低沉/暗色 (Dark)"
    if val < 3000: return "适中/温暖 (Warm)"
    if val < 5000: return "明亮/清脆 (Bright)"
    return "尖锐/刺耳 (Sharp)"


# 情绪推测规则
EMOTION_RULES = [
    (lambda b, m, e, c: b > 120 and m and e > 0.15, "Energetic/Excited", "高能兴奋"),
    (lambda b, m, e, c: b > 120 and not m and e > 0.15, "Intense/Aggressive", "激烈紧张"),
    (lambda b, m, e, c: 90 <= b <= 130 and m and e > 0.08, "Happy/Upbeat", "欢快愉悦"),
    (lambda b, m, e, c: 90 <= b <= 130 and not m and e > 0.08, "Passionate/Dramatic", "激情戏剧"),
    (lambda b, m, e, c: 60 <= b <= 90 and m and e > 0.04, "Calm/Peaceful", "平静祥和"),
    (lambda b, m, e, c: 60 <= b <= 90 and not m and e > 0.04, "Melancholic/Sad", "忧郁悲伤"),
    (lambda b, m, e, c: b < 60, "Slow/Meditative", "慢板冥想"),
    (lambda b, m, e, c: e < 0.03, "Ambient/Atmospheric", "氛围环境"),
    (lambda b, m, e, c: c > 4000, "Bright/Sparkling", "明亮闪耀"),
]

# ============================================================
# 和弦模板与常见进行式
# ============================================================

CHORD_TEMPLATES = []
for root_semi in range(12):
    major_vec = np.zeros(12)
    major_vec[root_semi] = 1.0
    major_vec[(root_semi + 4) % 12] = 0.8
    major_vec[(root_semi + 7) % 12] = 0.6
    CHORD_TEMPLATES.append((root_semi, f"{NOTE_NAMES_SHARP[root_semi]}maj", major_vec))
    minor_vec = np.zeros(12)
    minor_vec[root_semi] = 1.0
    minor_vec[(root_semi + 3) % 12] = 0.8
    minor_vec[(root_semi + 7) % 12] = 0.6
    CHORD_TEMPLATES.append((root_semi, f"{NOTE_NAMES_SHARP[root_semi]}min", minor_vec))

ROMAN_MAJOR_INTERVALS = {
    "I": 0, "ii": 2, "iii": 4, "IV": 5,
    "V": 7, "vi": 9, "vii°": 11,
}
ROMAN_MINOR_INTERVALS = {
    "i": 0, "ii°": 2, "III": 3, "iv": 5,
    "v": 7, "VI": 8, "VII": 10,
}

COMMON_PROGRESSIONS = [
    ("I-V-vi-IV", "流行经典进行 (Axis Progression)", "pop/rock/anthem", "major"),
    ("vi-IV-I-V", "伤感流行进行", "pop/emotional/sentimental", "major"),
    ("I-vi-IV-V", "50年代怀旧进行 (Doo-wop)", "oldies/retro/doo-wop", "major"),
    ("ii-V-I", "爵士终止式", "jazz/bossa/lounge", "major"),
    ("I-IV-V", "布鲁斯摇滚进行", "blues/rock/simple", "major"),
    ("I-IV-vi-V", "亚洲流行进行", "kpop/jpop/mandopop", "major"),
    ("vi-V-IV-III", "下行低音进行", "dramatic/epic/dark", "minor"),
    ("i-VI-III-VII", "小调和声进行", "minor/sad/cinematic", "minor"),
    ("i-iv-V-i", "小调终止进行", "minor/classical/gothic", "minor"),
    ("IV-I-V-vi", "变体流行进行", "alternative/pop", "major"),
    ("I-V-vi-iii-IV-I-IV-V", "卡农进行 (Pachelbel)", "classical/romantic/elegant", "major"),
    ("vi-IV-I-V", "EDM常用进行", "edm/house/dance", "major"),
]


def _roman_to_chord_name(roman, tonic_semi, mode):
    """将罗马数字转换为实际和弦名称。支持大小调互查。"""
    intervals = ROMAN_MAJOR_INTERVALS if mode == "major" else ROMAN_MINOR_INTERVALS
    clean = roman.replace("°", "")
    # 小调中可能出现大写 V（属和弦），回退到大调音程表查找
    if clean not in intervals:
        fallback = ROMAN_MINOR_INTERVALS if mode == "major" else ROMAN_MAJOR_INTERVALS
        if clean in fallback:
            intervals = fallback
    if clean not in intervals:
        return roman
    root = (tonic_semi + intervals[clean]) % 12
    if clean.isupper():
        quality = ""
    elif "°" in roman:
        quality = "dim"
    else:
        quality = "m"
    return f"{NOTE_NAMES_SHARP[root]}{quality}"


def _chord_to_roman(chord_name: str, tonic_semi: int, mode: str) -> str:
    """将和弦名称转换为罗马数字（基于当前调性）。"""
    # 解析和弦名 e.g. "Cmaj" → root=C, quality=major
    if len(chord_name) < 1:
        return ""
    # 提取根音
    root_str = chord_name[0]
    if len(chord_name) > 1 and chord_name[1] == "#":
        root_str = chord_name[:2]
    try:
        root_semi = NOTE_NAMES_SHARP.index(root_str)
    except ValueError:
        return ""
    # 判断和弦性质
    rest = chord_name[len(root_str):]
    if "min" in rest:
        quality = "minor"
    elif "maj" in rest:
        quality = "major"
    else:
        quality = "major"  # 默认大三

    # 映射到罗马数字
    intervals = ROMAN_MAJOR_INTERVALS if mode == "major" else ROMAN_MINOR_INTERVALS
    interval = (root_semi - tonic_semi) % 12
    # 先在当前模式查找，找不到则回退到另一模式
    for roman, semi in list(intervals.items()):
        if semi == interval:
            if mode == "major":
                if quality == "minor" and roman.islower():
                    return roman
                if quality == "major" and roman.isupper():
                    return roman
            else:
                if quality == "minor" and roman.islower():
                    return roman
                if quality == "major" and roman.isupper():
                    return roman
            return roman
    # 回退到另一模式
    fallback_intervals = ROMAN_MINOR_INTERVALS if mode == "major" else ROMAN_MAJOR_INTERVALS
    for roman, semi in list(fallback_intervals.items()):
        if semi == interval:
            return roman
    return ""


# ============================================================
# 数据结构
# ============================================================

@dataclass
class BasicInfo:
    filepath: str = ""
    format_name: str = ""
    duration_sec: float = 0.0
    sample_rate: int = 0
    bitrate_kbps: int = 0
    channels: int = 0
    codec: str = ""


@dataclass
class TempoInfo:
    bpm: float = 0.0
    bpm_confidence: float = 0.0
    tempo_class: str = ""
    tempo_class_cn: str = ""
    beat_strength: float = 0.0


@dataclass
class KeyInfo:
    detected_key: str = ""
    detected_mode: str = ""  # "major" / "minor"
    confidence: float = 0.0
    alternative_keys: list = field(default_factory=list)


@dataclass
class RhythmInfo:
    time_signature: str = "4/4"
    onset_rate: float = 0.0
    rhythm_complexity: str = ""
    rhythm_complexity_cn: str = ""
    syncopation_level: str = ""
    syncopation_level_cn: str = ""
    swing_ratio: float = 0.0


@dataclass
class SpectralInfo:
    centroid_mean: float = 0.0
    centroid_std: float = 0.0
    bandwidth_mean: float = 0.0
    rolloff_mean: float = 0.0
    contrast_mean: list = field(default_factory=list)
    zero_crossing_rate: float = 0.0
    timbre_description: str = ""


@dataclass
class EnergyInfo:
    rms_mean: float = 0.0
    rms_std: float = 0.0
    rms_max: float = 0.0
    dynamic_range_db: float = 0.0
    crest_factor: float = 0.0
    compression_feel: str = ""
    compression_feel_cn: str = ""


@dataclass
class InstrumentInfo:
    harmonic_energy_ratio: float = 0.0
    percussive_energy_ratio: float = 0.0
    likely_instruments: list = field(default_factory=list)
    has_drums: bool = False
    has_bass: bool = False
    has_keys_synth: bool = False
    has_strings: bool = False
    has_guitar: bool = False
    has_brass: bool = False
    is_orchestral: bool = False
    is_electronic: bool = False


@dataclass
class VocalInfo:
    has_vocals: bool = False
    vocal_gender: str = ""
    vocal_range_approx: str = ""
    vocal_style_hints: list = field(default_factory=list)
    vocal_energy_ratio: float = 0.0
    is_rap: bool = False


@dataclass
class StructureInfo:
    num_segments: int = 0
    estimated_sections: list = field(default_factory=list)
    has_intro: bool = False
    has_outro: bool = False
    has_bridge: bool = False
    chorus_count: int = 0


@dataclass
class MoodInfo:
    primary_emotion_en: str = ""
    primary_emotion_cn: str = ""
    secondary_emotions: list = field(default_factory=list)
    valence_estimate: str = ""
    arousal_estimate: str = ""


@dataclass
class HarmonyInfo:
    """和声走向 / 和弦进行"""
    detected_chords: list = field(default_factory=list)     # 检测到的和弦 [{root, quality, confidence}, ...]
    estimated_progression: str = ""   # 罗马数字进行式 eg. "I-V-vi-IV"
    progression_confidence: float = 0.0  # 进行式置信度
    chord_complexity: str = ""        # simple / moderate / complex
    root_motion_pattern: str = ""     # 根音移动描述


@dataclass
class ProductionInfo:
    reverb_level: str = ""
    reverb_level_cn: str = ""
    stereo_width: str = ""
    stereo_width_cn: str = ""
    loudness_lufs: float = 0.0
    production_era: str = ""
    production_era_cn: str = ""
    mix_quality: str = ""


@dataclass
class GenreStyleInfo:
    """曲风归类 — 基于已有12维度推理最可能的音乐流派"""
    primary_genre_en: str = ""       # 主曲风英文
    primary_genre_cn: str = ""       # 主曲风中文
    secondary_genres: list = field(default_factory=list)  # 次要曲风 [{en, cn, confidence}]
    genre_confidence: float = 0.0    # 主曲风置信度
    genre_tags_en: list = field(default_factory=list)     # 可用于 Suno 的曲风标签(英文)
    genre_tags_cn: list = field(default_factory=list)     # 曲风标签(中文)
    regional_style: str = ""         # 地域风格提示 e.g. "东方", "拉丁", "中东风"
    cultural_influence: str = ""     # 文化影响描述


# ---- 曲风归类规则引擎 ----

# 每条规则: (名称_en, 名称_cn, 风格标签列表_en, 评分函数(bpm, is_major, mood_en, inst, vocal, rhythm, spectral, energy, production) -> float(0~100))
GENRE_RULES = []

def _gr(p_en, p_cn, tags_en, tags_cn, score_fn):
    GENRE_RULES.append((p_en, p_cn, tags_en, tags_cn, score_fn))

# 1. 流行 (Pop)
_gr("Pop", "流行", ["pop"], ["流行"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        40 + (0 if 90 <= b <= 130 else -15) + (5 if i else 0)
        + (5 if v.has_vocals else -10) + (10 if m and "happy" in m.lower() or "upbeat" in m.lower() else 0)
        + (5 if r.rhythm_complexity == "low" or r.rhythm_complexity == "medium" else -5)
        + (5 if pr.compression_feel.startswith("light") or pr.compression_feel.startswith("medium") else 0)
    ))

# 2. 摇滚 (Rock)
_gr("Rock", "摇滚", ["rock"], ["摇滚"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        30 + (10 if 100 <= b <= 160 else -5)
        + (5 if not i else -5) + (10 if ins.has_guitar else -15)
        + (5 if ins.has_drums else -10) + (5 if e.rms_mean > 0.05 else 0)
        + (5 if e.dynamic_range_db > 8 else 0)
    ))

# 3. 电子/EDM
_gr("Electronic / EDM", "电子/舞曲", ["electronic", "edm"], ["电子", "舞曲"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        30 + (10 if b >= 120 else (5 if b >= 100 else -10))
        + (10 if ins.is_electronic else -15) + (5 if ins.has_keys_synth else 0)
        + (5 if ins.percussive_energy_ratio > 0.2 else 0)
        + (10 if pr.compression_feel == "heavy_compressed" else 0)
        + (5 if s.centroid_mean > 3000 else 0)
    ))

# 4. 爵士 (Jazz)
_gr("Jazz", "爵士", ["jazz"], ["爵士"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        25 + (10 if 60 <= b <= 180 else 0) + (5 if r.syncopation_level == "high" or r.syncopation_level == "medium" else 0)
        + (5 if r.swing_ratio > 0.3 else 0)
        + (5 if r.rhythm_complexity == "high" or r.rhythm_complexity == "medium" else 0)
        + (5 if ins.has_brass else 0) + (5 if not ins.has_drums or ins.percussive_energy_ratio < 0.2 else 0)
        + (10 if e.dynamic_range_db > 10 else 0)
        + (5 if "melancholic" in m.lower() or "calm" in m.lower() or "passionate" in m.lower() else 0)
    ))

# 5. 古典 (Classical)
_gr("Classical", "古典", ["classical"], ["古典"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        25 + (10 if ins.is_orchestral else -10)
        + (10 if ins.has_strings else -5) + (5 if e.dynamic_range_db > 12 else 0)
        + (5 if ins.harmonic_energy_ratio > 0.6 else 0)
        + (5 if not ins.has_drums or ins.percussive_energy_ratio < 0.1 else -5)
        + (5 if pr.reverb_level == "high" or pr.reverb_level == "medium" else 0)
        + (10 if b < 120 else 0)
    ))

# 6. 民谣 (Folk)
_gr("Folk", "民谣", ["folk", "acoustic"], ["民谣", "原声"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        30 + (10 if 70 <= b <= 120 else -5)
        + (5 if ins.has_guitar else 0) + (5 if v.has_vocals else -10)
        + (5 if v.vocal_gender and "男" in v.vocal_gender else 0)
        + (10 if not ins.is_electronic else -15)
        + (5 if r.rhythm_complexity == "low" or r.rhythm_complexity == "medium" else 0)
        + (5 if pr.reverb_level == "dry" or pr.reverb_level == "low" else 0)
    ))

# 7. R&B / Soul
_gr("R&B / Soul", "R&B/灵魂", ["rnb", "soul"], ["R&B", "灵魂乐"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        25 + (10 if 60 <= b <= 100 else -5)
        + (5 if r.swing_ratio > 0.25 else 0) + (5 if v.has_vocals else -15)
        + (5 if v.vocal_gender and "女" in v.vocal_gender else 0)
        + (5 if not ins.is_electronic else 0)
        + (5 if "passionate" in m.lower() or "melancholic" in m.lower() or "calm" in m.lower() else 0)
    ))

# 8. 说唱/嘻哈 (Hip-Hop / Rap)
_gr("Hip-Hop / Rap", "说唱/嘻哈", ["hip-hop", "rap"], ["说唱", "嘻哈"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        20 + (15 if v.is_rap else -20) + (10 if ins.percussive_energy_ratio > 0.2 else -5)
        + (10 if 70 <= b <= 110 else -5) + (5 if not i else 0)
        + (5 if ins.has_bass else -5) + (5 if e.rms_mean > 0.05 else 0)
    ))

# 9. 中国风 (Chinese Traditional / Guofeng)
_gr("Chinese Traditional / Guofeng", "中国风/国风", ["chinese traditional", "guofeng"], ["中国风", "国风"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (10 if ins.has_strings else 0) + (5 if 60 <= b <= 100 else 0)
        + (5 if e.dynamic_range_db > 8 else 0)
        + (5 if "melancholic" in m.lower() or "calm" in m.lower() or "dramatic" in m.lower() else 0)
        + (5 if pr.reverb_level == "medium" or pr.reverb_level == "high" else 0)
        + (5 if s.centroid_mean < 3000 else 0)
    ))

# 10. 日本风 (J-Pop / Japanese-style)
_gr("J-Pop / Japanese-style", "日本风/J-Pop", ["j-pop", "japanese"], ["日本风", "J-Pop"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (10 if 110 <= b <= 180 else -5) + (10 if i else 0)
        + (5 if ins.has_keys_synth else 0) + (5 if ins.has_guitar else 0)
        + (5 if v.has_vocals else -10) + (5 if r.rhythm_complexity == "medium" else 0)
        + (5 if "happy" in m.lower() or "upbeat" in m.lower() or "energetic" in m.lower() else 0)
    ))

# 11. 韩国流行 (K-Pop)
_gr("K-Pop", "韩国流行/K-Pop", ["k-pop", "korean"], ["韩国流行", "K-Pop"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (10 if 100 <= b <= 140 else 0) + (5 if ins.is_electronic else 0)
        + (5 if ins.has_keys_synth else 0) + (5 if ins.percussive_energy_ratio > 0.2 else 0)
        + (10 if pr.compression_feel == "heavy_compressed" or pr.compression_feel == "light_compressed" else 0)
        + (5 if v.has_vocals else -10) + (5 if r.rhythm_complexity == "medium" else 0)
    ))

# 12. 阿拉伯/中东风 (Arabic / Middle Eastern)
_gr("Arabic / Middle Eastern", "阿拉伯/中东", ["arabic", "middle eastern"], ["阿拉伯风", "中东风"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (10 if 80 <= b <= 140 else 0)
        + (5 if ins.has_strings else 0) + (5 if r.swing_ratio > 0.3 else 0)
        + (5 if r.syncopation_level == "medium" or r.syncopation_level == "high" else 0)
        + (5 if "intense" in m.lower() or "passionate" in m.lower() or "energetic" in m.lower() else 0)
    ))

# 13. 印度风 (Indian / Bollywood)
_gr("Indian / Bollywood", "印度/宝莱坞", ["indian", "bollywood"], ["印度风", "宝莱坞"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (10 if 100 <= b <= 160 else 0)
        + (5 if ins.has_strings else 0) + (5 if ins.percussive_energy_ratio > 0.2 else 0)
        + (5 if r.rhythm_complexity == "high" or r.rhythm_complexity == "medium" else 0)
        + (5 if v.has_vocals else 0)
        + (5 if "happy" in m.lower() or "energetic" in m.lower() else 0)
    ))

# 14. 拉丁风 (Latin)
_gr("Latin", "拉丁", ["latin"], ["拉丁"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (10 if 100 <= b <= 140 else 0)
        + (10 if r.syncopation_level == "medium" or r.syncopation_level == "high" else -5)
        + (5 if ins.has_brass else 0) + (5 if r.swing_ratio > 0.25 else 0)
        + (5 if ins.percussive_energy_ratio > 0.2 else 0)
        + (5 if "energetic" in m.lower() or "happy" in m.lower() or "passionate" in m.lower() else 0)
    ))

# 15. 校园风 (Campus Folk / School Ballad)
_gr("Campus Folk / School Ballad", "校园风/校园民谣", ["campus folk", "school ballad", "acoustic"], ["校园风", "校园民谣"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        20 + (10 if 70 <= b <= 110 else -5) + (10 if i else 0)
        + (10 if ins.has_guitar else -5) + (5 if v.has_vocals else -10)
        + (5 if not ins.is_electronic else -10)
        + (5 if r.rhythm_complexity == "low" else 0)
        + (5 if "calm" in m.lower() or "melancholic" in m.lower() or "happy" in m.lower() else 0)
        + (5 if pr.reverb_level == "dry" or pr.reverb_level == "low" else 0)
    ))

# 16. 金属 (Metal)
_gr("Metal", "金属", ["metal"], ["金属"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        15 + (10 if b >= 120 else -10) + (10 if not i else -5)
        + (10 if ins.has_guitar else 0) + (10 if ins.has_drums else -10)
        + (5 if e.rms_mean > 0.08 else 0) + (5 if e.dynamic_range_db > 8 else 0)
        + (5 if "intense" in m.lower() or "aggressive" in m.lower() else 0)
    ))

# 17. 布鲁斯 (Blues)
_gr("Blues", "布鲁斯", ["blues"], ["布鲁斯"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        15 + (10 if 60 <= b <= 120 else -5)
        + (5 if ins.has_guitar else 0) + (5 if not i else -5)
        + (5 if r.syncopation_level == "medium" else 0)
        + (5 if "melancholic" in m.lower() or "passionate" in m.lower() else 0)
        + (5 if pr.production_era == "vintage" or pr.production_era == "classic" else 0)
    ))

# 18. 雷鬼 (Reggae)
_gr("Reggae", "雷鬼", ["reggae"], ["雷鬼"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (10 if 60 <= b <= 90 else -5)
        + (10 if r.rhythm_complexity == "medium" else 0)
        + (5 if i else -5) + (5 if ins.has_bass else 0)
        + (5 if "calm" in m.lower() or "happy" in m.lower() else 0)
    ))

# 19. 放克 (Funk)
_gr("Funk", "放克", ["funk"], ["放克"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (10 if 90 <= b <= 120 else -5)
        + (10 if r.syncopation_level == "medium" or r.syncopation_level == "high" else -5)
        + (5 if ins.has_bass else 0) + (5 if ins.has_brass else 0)
        + (5 if ins.has_guitar else 0) + (5 if r.swing_ratio > 0.2 else 0)
        + (5 if e.rms_mean > 0.05 else 0)
    ))

# 20. 新世纪 (New Age)
_gr("New Age", "新世纪", ["new age", "ambient"], ["新世纪", "氛围"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (5 if b < 100 else 0) + (5 if ins.has_keys_synth else 0)
        + (5 if e.dynamic_range_db > 10 else 0) + (10 if e.rms_mean < 0.03 else -5)
        + (5 if pr.reverb_level == "high" or pr.reverb_level == "medium" else 0)
        + (5 if not ins.has_drums or ins.percussive_energy_ratio < 0.1 else -5)
        + (5 if s.centroid_mean < 2500 else 0)
    ))

# 21. 氛围 (Ambient)
_gr("Ambient", "氛围/环境", ["ambient"], ["氛围音乐"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (10 if b < 90 else -5) + (10 if e.rms_mean < 0.03 else -10)
        + (5 if e.dynamic_range_db > 10 else 0)
        + (5 if pr.reverb_level == "high" or pr.reverb_level == "medium" else 0)
        + (5 if not v.has_vocals else -5) + (5 if ins.has_keys_synth else 0)
        + (5 if s.centroid_mean < 2500 else 0)
    ))

# 22. 电影配乐 (Cinematic / Orchestral)
_gr("Cinematic / Orchestral", "电影配乐/管弦", ["cinematic", "orchestral"], ["电影配乐", "管弦"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (15 if ins.is_orchestral else -10) + (10 if ins.has_strings else 0)
        + (5 if e.dynamic_range_db > 10 else 0) + (5 if ins.has_brass else 0)
        + (5 if "dramatic" in m.lower() or "epic" in m.lower() or "intense" in m.lower() else 0)
        + (5 if pr.reverb_level == "high" or pr.reverb_level == "medium" else 0)
    ))

# 23. 舞曲 (Dance)
_gr("Dance", "舞曲", ["dance"], ["舞曲"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (15 if b >= 120 else -5) + (10 if ins.is_electronic else -5)
        + (10 if ins.percussive_energy_ratio > 0.20 else -5)
        + (5 if pr.compression_feel == "heavy_compressed" else 0)
        + (5 if ins.has_bass else 0) + (5 if "energetic" in m.lower() or "happy" in m.lower() else 0)
    ))

# 24. 独立/另类 (Indie / Alternative)
_gr("Indie / Alternative", "独立/另类", ["indie", "alternative"], ["独立", "另类"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (5 if 80 <= b <= 140 else 0) + (5 if ins.has_guitar else 0)
        + (5 if r.rhythm_complexity == "medium" else 0)
        + (5 if e.dynamic_range_db > 8 else 0)
        + (5 if pr.production_era == "classic" or pr.production_era == "vintage" else 0)
    ))

# 25. 巴萨诺瓦 (Bossa Nova)
_gr("Bossa Nova", "巴萨诺瓦", ["bossa nova"], ["巴萨诺瓦"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (10 if 100 <= b <= 160 else -5) + (5 if ins.has_guitar else 0)
        + (5 if ins.has_strings else 0) + (5 if r.swing_ratio > 0.3 else 0)
        + (5 if r.rhythm_complexity == "medium" or r.rhythm_complexity == "high" else 0)
        + (5 if "calm" in m.lower() or "melancholic" in m.lower() else 0)
    ))

# 26. 童谣/儿歌 (Children's / Nursery)
_gr("Children's / Nursery", "童谣/儿歌", ["children", "nursery"], ["童谣", "儿歌"],
    lambda b,i,m,ins,v,r,s,e,pr: (
        10 + (10 if 80 <= b <= 130 else -5) + (10 if i else 0)
        + (5 if r.rhythm_complexity == "low" else 0)
        + (5 if "happy" in m.lower() or "calm" in m.lower() else 0)
        + (5 if not ins.is_electronic else -5)
    ))


@dataclass
class SongAnalysis:
    basic: BasicInfo = field(default_factory=BasicInfo)
    tempo: TempoInfo = field(default_factory=TempoInfo)
    key: KeyInfo = field(default_factory=KeyInfo)
    rhythm: RhythmInfo = field(default_factory=RhythmInfo)
    spectral: SpectralInfo = field(default_factory=SpectralInfo)
    energy: EnergyInfo = field(default_factory=EnergyInfo)
    instruments: InstrumentInfo = field(default_factory=InstrumentInfo)
    vocals: VocalInfo = field(default_factory=VocalInfo)
    structure: StructureInfo = field(default_factory=StructureInfo)
    mood: MoodInfo = field(default_factory=MoodInfo)
    harmony: HarmonyInfo = field(default_factory=HarmonyInfo)
    production: ProductionInfo = field(default_factory=ProductionInfo)
    genre: GenreStyleInfo = field(default_factory=GenreStyleInfo)

    def to_dict(self):
        return {
            "basic": asdict(self.basic),
            "tempo": asdict(self.tempo),
            "key": asdict(self.key),
            "rhythm": asdict(self.rhythm),
            "spectral": asdict(self.spectral),
            "energy": asdict(self.energy),
            "instruments": asdict(self.instruments),
            "vocals": asdict(self.vocals),
            "structure": asdict(self.structure),
            "mood": asdict(self.mood),
            "harmony": asdict(self.harmony),
            "production": asdict(self.production),
            "genre": asdict(self.genre),
        }


# ============================================================
# 核心分析器
# ============================================================

class AudioAnalyzer:
    """音频全面分析器"""

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        if not self.filepath.exists():
            raise FileNotFoundError(f"文件不存在: {filepath}")
        self.basic = BasicInfo(filepath=str(self.filepath.resolve()))
        self._y, self._sr = None, None
        self._y_harm, self._y_perc = None, None
        self._chroma = None
        self._tempo = None
        self._beat_frames = None
        self._onset_env = None
        self._stft, self._stft_freqs = None, None
        self._mfcc = None
        self._rms = None
        self.tempo = TempoInfo()
        self.key = KeyInfo()
        self.rhythm = RhythmInfo()
        self.spectral = SpectralInfo()
        self.energy = EnergyInfo()
        self.instruments = InstrumentInfo()
        self.vocals = VocalInfo()
        self.structure = StructureInfo()
        self.mood = MoodInfo()
        self.harmony = HarmonyInfo()
        self.production = ProductionInfo()
        self.genre = GenreStyleInfo()

    # ---- 基础信息 (ffprobe) ----

    def analyze_basic(self):
        try:
            result = subprocess.run(
                [FFPROBE, "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", str(self.filepath)],
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace"
            )
            if result.returncode != 0:
                print(f"[WARN] ffprobe error: {result.stderr}", file=sys.stderr)
                return self.basic
            data = json.loads(result.stdout)
            fmt = data.get("format", {})
            self.basic.format_name = fmt.get("format_name", "")
            try:
                self.basic.duration_sec = float(fmt.get("duration", 0))
            except (ValueError, TypeError):
                self.basic.duration_sec = 0.0
            try:
                self.basic.bitrate_kbps = int(int(fmt.get("bit_rate", 0)) / 1000)
            except (ValueError, TypeError):
                self.basic.bitrate_kbps = 0
            for s in data.get("streams", []):
                if s.get("codec_type") == "audio":
                    self.basic.sample_rate = int(s.get("sample_rate", 0))
                    self.basic.channels = int(s.get("channels", 0))
                    self.basic.codec = s.get("codec_name", "")
                    break
        except Exception as e:
            print(f"[WARN] ffprobe failed: {e}", file=sys.stderr)
        return self.basic

    # ---- 音频加载 ----

    def _load_audio(self, sr: int = 22050) -> bool:
        if self._y is not None:
            return True
        try:
            import librosa
            # 尝试直接加载
            self._y, self._sr = librosa.load(str(self.filepath), sr=sr, mono=True, duration=600)
            if self.basic.sample_rate == 0:
                self.basic.sample_rate = self._sr
            return True
        except Exception as e:
            # 直接加载失败，尝试用 ASCII 临时文件绕过 Unicode 路径问题
            try:
                import shutil as _shutil
                tmp = tempfile.NamedTemporaryFile(suffix=self.filepath.suffix, delete=False)
                tmp_path = tmp.name
                tmp.close()
                _shutil.copy2(str(self.filepath), tmp_path)
                import librosa as lr
                self._y, self._sr = lr.load(tmp_path, sr=sr, mono=True, duration=600)
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                if self.basic.sample_rate == 0:
                    self.basic.sample_rate = self._sr
                return True
            except Exception as e2:
                print(f"[ERROR] Audio load failed (direct+temp): {e} | {e2}", file=sys.stderr)
                return False

    def _get_stft(self):
        """惰性缓存 STFT（频谱分析及乐器推测共用）。"""
        if self._stft is not None:
            return self._stft, self._stft_freqs
        self._stft = np.abs(librosa.stft(self._y))
        self._stft_freqs = librosa.fft_frequencies(sr=self._sr)
        return self._stft, self._stft_freqs

    def _get_mfcc(self):
        """惰性缓存 MFCC（人声分析及曲式结构共用）。"""
        if self._mfcc is not None:
            return self._mfcc
        self._mfcc = librosa.feature.mfcc(y=self._y, sr=self._sr, n_mfcc=13)
        return self._mfcc

    def _get_rms(self):
        """惰性缓存 RMS（能量分析及制作特征共用）。"""
        if self._rms is not None:
            return self._rms
        self._rms = librosa.feature.rms(y=self._y)
        return self._rms

    # ---- BPM / 速度 ----

    def analyze_tempo(self):
        if not self._load_audio():
            return self.tempo
        import librosa
        onset_env = librosa.onset.onset_strength(y=self._y, sr=self._sr)
        self._onset_env = onset_env
        tempo_val, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=self._sr)
        if isinstance(tempo_val, np.ndarray):
            tempo_val = float(tempo_val[0])
        self._tempo = tempo_val
        self._beat_frames = beats
        if len(beats) > 0 and len(onset_env) > 0:
            bi = librosa.frames_to_samples(beats)
            valid = bi[bi < len(onset_env)]
            bs = float(np.mean(onset_env[valid])) if len(valid) > 0 else float(np.mean(onset_env))
        else:
            bs = 0.0
        ac = librosa.autocorrelate(onset_env, max_size=len(onset_env)//2)
        conf = min(1.0, float(np.max(ac[10:])) * 2) if len(ac) > 10 else 0.0
        tc, tcn = "", ""
        for lo, hi, desc in BPM_TO_TEMPO:
            if lo <= tempo_val < hi:
                tc = desc
                tcn = BPM_TEMPO_CN.get(desc, desc)
                break
        self.tempo = TempoInfo(bpm=round(tempo_val, 1), bpm_confidence=round(conf, 3),
                               tempo_class=tc, tempo_class_cn=tcn, beat_strength=round(bs, 4))
        return self.tempo

    # ---- 调式分析 ----

    def analyze_key(self):
        if not self._load_audio():
            return self.key
        import librosa
        src_y = self._y_harm if self._y_harm is not None else self._y
        try:
            chroma = librosa.feature.chroma_cqt(y=src_y, sr=self._sr, bins_per_octave=36)
        except Exception:
            chroma = librosa.feature.chroma_stft(y=src_y, sr=self._sr)
        self._chroma = chroma
        chroma_mean = np.mean(chroma, axis=1)
        chroma_mean = chroma_mean / (np.sum(chroma_mean) + 1e-10)
        correlations = []
        for tonic in range(12):
            rm = np.roll(MAJOR_PROFILE, tonic); rm = rm / (np.sum(rm) + 1e-10)
            rn = np.roll(MINOR_PROFILE, tonic); rn = rn / (np.sum(rn) + 1e-10)
            corr_maj = np.corrcoef(chroma_mean, rm)[0, 1]
            corr_min = np.corrcoef(chroma_mean, rn)[0, 1]
            if np.isnan(corr_maj):
                corr_maj = 0.0
            if np.isnan(corr_min):
                corr_min = 0.0
            correlations.append((corr_maj, NOTE_NAMES_SHARP[tonic], "major"))
            correlations.append((corr_min, NOTE_NAMES_SHARP[tonic], "minor"))
        correlations.sort(key=lambda x: x[0], reverse=True)
        best = correlations[0]
        confidence = max(0.0, min(1.0, float(best[0])))
        if best[0] < 0.3:
            confidence = best[0] / 0.3
        alternatives = []
        for corr, note, mode in correlations[1:4]:
            if corr > 0.1:
                alternatives.append({"key": note, "mode": mode, "confidence": round(float(corr), 3)})
        self.key = KeyInfo(detected_key=best[1], detected_mode=best[2],
                           confidence=round(confidence, 3), alternative_keys=alternatives)
        return self.key

    # ---- 节奏特征 ----

    def analyze_rhythm(self):
        info = RhythmInfo()
        if not self._load_audio():
            return info
        import librosa
        onset_frames = librosa.onset.onset_detect(y=self._y, sr=self._sr, backtrack=True, units="frames")
        if len(onset_frames) > 0 and self.basic.duration_sec > 0:
            info.onset_rate = round(len(onset_frames) / self.basic.duration_sec, 2)
        if len(onset_frames) >= 3:
            onset_intervals = np.diff(onset_frames)
            cv = np.std(onset_intervals) / (np.mean(onset_intervals) + 1e-10)
            if cv < 0.3:
                info.rhythm_complexity = "low"; info.rhythm_complexity_cn = "低 (规则/简单)"
            elif cv < 0.7:
                info.rhythm_complexity = "medium"; info.rhythm_complexity_cn = "中 (有变化/丰富)"
            else:
                info.rhythm_complexity = "high"; info.rhythm_complexity_cn = "高 (复杂/不规则)"
        if self._beat_frames is not None and len(self._beat_frames) >= 1 and len(onset_frames) >= 1:
            bfs = set(self._beat_frames)
            off_beat = sum(1 for o in onset_frames if o not in bfs)
            sync_ratio = off_beat / max(1, len(onset_frames))
            if sync_ratio < 0.2:
                info.syncopation_level = "low"; info.syncopation_level_cn = "低 (正拍为主)"
            elif sync_ratio < 0.5:
                info.syncopation_level = "medium"; info.syncopation_level_cn = "中 (适度切分)"
            else:
                info.syncopation_level = "high"; info.syncopation_level_cn = "高 (强烈切分)"
            info.swing_ratio = round(sync_ratio, 3)
        self.rhythm = info
        return info

    # ---- 频谱特征 ----

    def analyze_spectral(self):
        info = SpectralInfo()
        if not self._load_audio():
            return info
        import librosa
        cent = librosa.feature.spectral_centroid(y=self._y, sr=self._sr)
        info.centroid_mean = round(float(np.mean(cent)), 1)
        info.centroid_std = round(float(np.std(cent)), 1)
        bw = librosa.feature.spectral_bandwidth(y=self._y, sr=self._sr)
        info.bandwidth_mean = round(float(np.mean(bw)), 1)
        ro = librosa.feature.spectral_rolloff(y=self._y, sr=self._sr, roll_percent=0.85)
        info.rolloff_mean = round(float(np.mean(ro)), 1)
        try:
            ct = librosa.feature.spectral_contrast(y=self._y, sr=self._sr, n_bands=6)
            info.contrast_mean = [round(float(np.mean(b)), 2) for b in ct]
        except Exception:
            pass
        zcr = librosa.feature.zero_crossing_rate(self._y)
        info.zero_crossing_rate = round(float(np.mean(zcr)), 5)
        info.timbre_description = centroid_to_brightness(info.centroid_mean)
        self.spectral = info
        return info

    # ---- 能量动态 ----

    def analyze_energy(self):
        info = EnergyInfo()
        if not self._load_audio():
            return info
        import librosa
        rms = self._get_rms()
        rf = rms.flatten()
        info.rms_mean = round(float(np.mean(rf)), 6)
        info.rms_std = round(float(np.std(rf)), 6)
        info.rms_max = round(float(np.max(rf)), 6)
        nonzero = rf[rf > 1e-10]
        if len(nonzero) > 0:
            rms_db = 20 * np.log10(nonzero)
            info.dynamic_range_db = round(float(np.max(rms_db) - np.min(rms_db)), 1)
        if info.rms_mean > 0:
            info.crest_factor = round(info.rms_max / info.rms_mean, 2)
        if info.dynamic_range_db > 15:
            info.compression_feel = "high_dynamic"; info.compression_feel_cn = "高动态 (自然/原声感)"
        elif info.dynamic_range_db > 8:
            info.compression_feel = "medium_dynamic"; info.compression_feel_cn = "中等动态 (流行/摇滚常见)"
        elif info.dynamic_range_db > 4:
            info.compression_feel = "light_compressed"; info.compression_feel_cn = "轻度压缩 (现代流行)"
        else:
            info.compression_feel = "heavy_compressed"; info.compression_feel_cn = "高度压缩 (响度战争/电子)"
        self.energy = info
        return info

    # ---- 乐器推测 ----

    def analyze_instruments(self):
        info = InstrumentInfo()
        if not self._load_audio():
            return info
        import librosa
        try:
            self._y_harm, self._y_perc = librosa.effects.hpss(self._y, margin=(1.0, 3.0))
            he = np.sum(self._y_harm ** 2); pe = np.sum(self._y_perc ** 2)
            total = he + pe + 1e-10
            info.harmonic_energy_ratio = round(float(he / total), 3)
            info.percussive_energy_ratio = round(float(pe / total), 3)
        except Exception:
            info.harmonic_energy_ratio = 0.5
            info.percussive_energy_ratio = 0.5
        info.has_drums = info.percussive_energy_ratio > 0.15
        import librosa
        stft, freqs = self._get_stft()
        total_e = np.sum(stft) + 1e-10
        low_band = stft[freqs < 250, :]
        info.has_bass = float(np.sum(low_band) / total_e) > 0.2
        mid_band = stft[(freqs >= 250) & (freqs < 2000), :]
        high_band = stft[(freqs >= 2000) & (freqs < 8000), :]
        mid_e = float(np.sum(mid_band) / total_e)
        high_e = float(np.sum(high_band) / total_e)
        info.has_guitar = mid_e > 0.15 and info.harmonic_energy_ratio > 0.4
        info.has_keys_synth = high_e > 0.08 or (info.harmonic_energy_ratio > 0.5 and mid_e > 0.2)
        info.has_strings = info.harmonic_energy_ratio > 0.6 and info.percussive_energy_ratio < 0.1
        info.has_brass = high_e > 0.12 and info.harmonic_energy_ratio > 0.35
        info.is_orchestral = info.has_strings and info.harmonic_energy_ratio > 0.5 and self.energy.dynamic_range_db > 12
        info.is_electronic = (info.compression_feel == "heavy_compressed" if hasattr(info, 'compression_feel') else self.energy.compression_feel.startswith("heavy")) and high_e > 0.1 and info.percussive_energy_ratio > 0.2
        likely = []
        if info.has_drums: likely.append("鼓组/打击乐 (Drums/Percussion)")
        if info.has_bass: likely.append("贝斯 (Bass)")
        if info.has_guitar: likely.append("吉他 (Guitar)")
        if info.has_keys_synth: likely.append("键盘/合成器 (Keys/Synth)")
        if info.has_strings: likely.append("弦乐 (Strings)")
        if info.has_brass: likely.append("铜管 (Brass)")
        if info.is_electronic: likely.append("电子元素 (Electronic Elements)")
        if info.is_orchestral: likely.append("管弦编制 (Orchestral)")
        info.likely_instruments = likely
        self.instruments = info
        return info

    # ---- 人声特征 ----

    def analyze_vocals(self):
        info = VocalInfo()
        if not self._load_audio():
            return info
        import librosa
        stft, freqs = self._get_stft()
        vocal_band_mask = (freqs >= 80) & (freqs <= 4000)
        total_energy = np.sum(stft) + 1e-10
        vocal_band_energy = float(np.sum(stft[vocal_band_mask, :]) / total_energy)
        info.has_vocals = vocal_band_energy > 0.15
        if info.has_vocals:
            info.vocal_energy_ratio = round(vocal_band_energy, 3)
            try:
                mfcc = self._get_mfcc()
                mfcc_mean = np.mean(mfcc, axis=1)
                lmfe = float(np.sum(np.abs(mfcc_mean[1:5])))
                hmfe = float(np.sum(np.abs(mfcc_mean[5:12])) + 1e-10)
                mfcc_ratio = lmfe / hmfe
                styles = []
                if info.vocal_energy_ratio > 0.4:
                    styles.append("vocal-forward (人声突出)")
                if mfcc_ratio > 2.0:
                    styles.extend(["breathy (气声)", "soft (柔和)"])
                elif mfcc_ratio < 1.0:
                    styles.append("bright/powerful (明亮有力)")
                if vocal_band_energy > 0.3:
                    styles.append("full-bodied (饱满)")
                if self.rhythm.onset_rate > 4.0 and vocal_band_energy > 0.2:
                    info.is_rap = True
                    styles.append("rap/spoken (说唱)")
                info.vocal_style_hints = styles
            except Exception:
                pass
            try:
                f0, voiced_flag, _ = librosa.pyin(self._y, fmin=80, fmax=600, sr=self._sr)
                voiced_f0 = f0[voiced_flag]
                if len(voiced_f0) > 10:
                    mf = float(np.median(voiced_f0))
                    if mf < 165:
                        info.vocal_gender = "男声倾向 (Male)"
                        info.vocal_range_approx = "中低音区"
                    elif mf < 255:
                        info.vocal_gender = "中性/不确定 (Ambiguous)"
                        info.vocal_range_approx = "中音区"
                    else:
                        info.vocal_gender = "女声倾向 (Female)"
                        info.vocal_range_approx = "中高音区"
            except Exception:
                pass
        self.vocals = info
        return info

    # ---- 曲式结构 ----

    def analyze_structure(self):
        info = StructureInfo()
        if not self._load_audio():
            return info
        import librosa
        try:
            mfcc = self._get_mfcc()
            R = librosa.segment.recurrence_matrix(mfcc, mode="affinity", sym=True)
            lag = librosa.segment.timelag_filter(R)
            if len(lag) > 1:
                bounds = librosa.segment.agglomerative(lag, k=min(12, len(lag)//10 + 3))
                bt = librosa.frames_to_time(bounds, sr=self._sr)
                info.num_segments = len(bt)
                info.estimated_sections = [{"start_sec": round(float(bt[i]), 1)} for i in range(len(bt))]
        except Exception:
            pass
        ns = info.num_segments
        if ns >= 4:
            info.has_intro = True; info.has_outro = True
            info.chorus_count = max(1, (ns - 4) // 2)
        if ns >= 5:
            info.has_bridge = True
        self.structure = info
        return info

    # ---- 情绪氛围 ----

    def analyze_mood(self):
        info = MoodInfo()
        bpm = self.tempo.bpm
        is_major = self.key.detected_mode == "major"
        energy = self.energy.rms_mean
        centroid = self.spectral.centroid_mean
        matched = []
        for rule in EMOTION_RULES:
            try:
                if rule[0](bpm, is_major, energy, centroid):
                    matched.append(rule)
            except Exception:
                continue
        if matched:
            info.primary_emotion_en = matched[0][1]
            info.primary_emotion_cn = matched[0][2]
            for m in matched[1:3]:
                info.secondary_emotions.append({"en": m[1], "cn": m[2]})
        else:
            info.primary_emotion_en = "Neutral"; info.primary_emotion_cn = "中性"
        info.valence_estimate = "积极 (Positive)" if is_major else "中性偏消极 (Neutral-Negative)"
        if bpm > 120 and energy > 0.1:
            info.arousal_estimate = "高唤醒 (High)"
        elif bpm < 80 or energy < 0.03:
            info.arousal_estimate = "低唤醒 (Low)"
        else:
            info.arousal_estimate = "中唤醒 (Medium)"
        self.mood = info
        return info

    # ---- 和声走向 / 和弦进行 ----

    def analyze_harmony(self):
        """基于色度图模板匹配检测和弦，并推测常见进行式。"""
        info = HarmonyInfo()
        if not self._load_audio():
            self.harmony = info
            return info
        import librosa

        # 计算色度图（确保已计算，复用或重新计算）
        chroma = self._chroma
        if chroma is None:
            src_y = self._y_harm if self._y_harm is not None else self._y
            try:
                chroma = librosa.feature.chroma_cqt(y=src_y, sr=self._sr, bins_per_octave=36)
            except Exception:
                chroma = librosa.feature.chroma_stft(y=src_y, sr=self._sr)

        # 每隔一定帧数采样色度图，降低计算量
        n_frames = chroma.shape[1]
        sample_step = max(1, n_frames // 60)  # 约60个采样点
        detected = []

        for i in range(0, n_frames, sample_step):
            frame_vec = chroma[:, i]
            frame_vec = frame_vec / (np.sum(frame_vec) + 1e-10)
            # 与所有和弦模板做相关
            best_score, best_chord = -99, ""
            for root_semi, name, tmpl in CHORD_TEMPLATES:
                score = np.corrcoef(frame_vec, tmpl)[0, 1]
                if np.isnan(score):
                    score = 0.0
                if score > best_score:
                    best_score = score
                    best_chord = name
            if best_score > 0.3:
                detected.append((best_chord, float(best_score), i))

        # 取置信度最高的和弦
        top_chords = {}
        for chord, score, idx in detected:
            if chord not in top_chords or score > top_chords[chord]["confidence"]:
                top_chords[chord] = {"name": chord, "confidence": round(score, 3)}

        info.detected_chords = sorted(
            top_chords.values(), key=lambda x: x["confidence"], reverse=True
        )[:8]

        # 推测根音移动模式
        chord_sequence = [c[0] for c in detected]
        if len(chord_sequence) >= 4:
            # 简化：取前4个不同和弦
            unique_seq = []
            for c in chord_sequence:
                if not unique_seq or c != unique_seq[-1]:
                    unique_seq.append(c)
                if len(unique_seq) >= 8:
                    break

            # 尝试映射到常见进行式
            tonic_note = self.key.detected_key or "C"
            mode = self.key.detected_mode or "major"
            try:
                tonic_semi = NOTE_NAMES_SHARP.index(tonic_note)
            except ValueError:
                tonic_semi = 0

            # 将检测到的和弦序列映射为罗马数字
            roman_seq = []
            for cname in unique_seq[:8]:
                roman = _chord_to_roman(cname, tonic_semi, mode)
                if roman:
                    roman_seq.append(roman)

            # 与常见进行式匹配
            best_prog, best_score_p, best_prog_cn, best_style = "", 0.0, "", ""
            roman_str = "-".join(roman_seq)

            for prog_str, cn_name, style, prog_mode in COMMON_PROGRESSIONS:
                if prog_mode != "generic" and prog_mode != mode:
                    continue
                # 滑动窗口匹配
                prog_parts = prog_str.split("-")
                if len(prog_parts) <= len(roman_seq):
                    for offset in range(len(roman_seq) - len(prog_parts) + 1):
                        matches = sum(
                            1 for j, rp in enumerate(prog_parts)
                            if roman_seq[offset + j] == rp
                        )
                        score = matches / len(prog_parts)
                        if score > best_score_p:
                            best_score_p = score
                            best_prog = prog_str
                            best_prog_cn = cn_name
                            best_style = style

            info.estimated_progression = best_prog
            info.progression_confidence = round(best_score_p, 3)

            # 和弦复杂度
            unique_count = len(set(chord_sequence))
            if unique_count <= 3:
                info.chord_complexity = "简单 (2-3个和弦循环)"
            elif unique_count <= 6:
                info.chord_complexity = "中等 (4-6个和弦)"
            else:
                info.chord_complexity = "复杂 (7个以上和弦变化)"

            # 根音移动模式
            if best_prog:
                actual_names = " → ".join(
                    _roman_to_chord_name(r, tonic_semi, mode)
                    for r in best_prog.split("-")
                )
                info.root_motion_pattern = (
                    f"推测进行式: {best_prog} ({best_prog_cn}) | "
                    f"在此调下: {actual_names} "
                    f"| 置信度: {int(best_score_p*100)}%"
                )
            elif roman_seq:
                names = " → ".join(
                    _roman_to_chord_name(r, tonic_semi, mode) for r in roman_seq[:4]
                )
                info.root_motion_pattern = f"未匹配已知进行式，检测到的和弦序列: {names} ..."

        self.harmony = info
        return info

    def analyze_production(self):
        info = ProductionInfo()
        if not self._load_audio():
            return info
        import librosa
        try:
            stft, _ = self._get_stft()
            sf = np.mean(np.abs(np.diff(stft, axis=1)), axis=1)
            dr = np.mean(sf[-10:]) / (np.mean(sf[:10]) + 1e-10)
            if dr < 0.3:
                info.reverb_level = "high"; info.reverb_level_cn = "高混响 (大空间感)"
            elif dr < 0.6:
                info.reverb_level = "medium"; info.reverb_level_cn = "中等混响 (自然的空间感)"
            elif dr < 0.9:
                info.reverb_level = "low"; info.reverb_level_cn = "低混响 (干声/近距感)"
            else:
                info.reverb_level = "dry"; info.reverb_level_cn = "几乎无混响 (紧贴感)"
        except Exception:
            info.reverb_level = "unknown"; info.reverb_level_cn = "无法评估"
        try:
            if self.basic.channels >= 2:
                y_stereo, _ = librosa.load(str(self.filepath), sr=22050, mono=False, duration=120)
                if y_stereo.ndim == 2 and y_stereo.shape[0] >= 2:
                    mid = y_stereo[0] + y_stereo[1]; side = y_stereo[0] - y_stereo[1]
                    me = np.sum(mid**2); se = np.sum(side**2)
                    wr = se / (me + se + 1e-10)
                    if wr > 0.3:
                        info.stereo_width = "very_wide"; info.stereo_width_cn = "极宽 (宽阔立体声场)"
                    elif wr > 0.15:
                        info.stereo_width = "standard"; info.stereo_width_cn = "标准 (正常立体声)"
                    elif wr > 0.05:
                        info.stereo_width = "narrow"; info.stereo_width_cn = "狭窄 (偏单声道感)"
                    else:
                        info.stereo_width = "mono"; info.stereo_width_cn = "几乎单声道"
                else:
                    info.stereo_width = "mono"; info.stereo_width_cn = "单声道 (Mono)"
            else:
                info.stereo_width = "mono"; info.stereo_width_cn = "单声道 (Mono)"
        except Exception:
            info.stereo_width = "unknown"; info.stereo_width_cn = "无法评估"
        try:
            rms = self._get_rms()
            info.loudness_lufs = round(float(20 * np.log10(np.mean(rms) + 1e-10)), 1)
        except Exception:
            info.loudness_lufs = -999.0
        dr = self.energy.dynamic_range_db
        if dr > 12:
            info.production_era = "vintage"; info.production_era_cn = "Vintage/原声 (高动态)"
        elif dr > 6:
            info.production_era = "classic"; info.production_era_cn = "Classic/经典制作 (2000s前后)"
        else:
            info.production_era = "modern"; info.production_era_cn = "Modern/现代制作 (响度优先)"
        qs = 0
        if 6 <= dr <= 15: qs += 1
        if self.spectral.centroid_mean < 5000: qs += 1
        if 40 <= self.tempo.bpm <= 220: qs += 1
        info.mix_quality = "良好/专业级" if qs >= 2 else "一般/需要改善"
        self.production = info
        return info

    def analyze_genre(self):
        """基于已有12维度数据，通过规则引擎推理曲风归类。"""
        info = GenreStyleInfo()
        bpm = self.tempo.bpm
        is_major = self.key.detected_mode == "major"
        mood_en = self.mood.primary_emotion_en.lower() if self.mood.primary_emotion_en else "neutral"
        inst = self.instruments
        vocal = self.vocals
        rhythm = self.rhythm
        spectral = self.spectral
        energy = self.energy
        prod = self.production

        # 对所有曲风规则打分
        scored = []
        for p_en, p_cn, tags_en, tags_cn, score_fn in GENRE_RULES:
            try:
                score = score_fn(bpm, is_major, mood_en, inst, vocal, rhythm, spectral, energy, prod)
            except Exception:
                score = 0.0
            score = max(0.0, min(100.0, score))
            scored.append((p_en, p_cn, tags_en, tags_cn, score))

        # 按分数降序排列
        scored.sort(key=lambda x: x[4], reverse=True)

        if scored:
            best = scored[0]
            info.primary_genre_en = best[0]
            info.primary_genre_cn = best[1]
            info.genre_confidence = round(best[4] / 100.0, 3)
            info.genre_tags_en = best[2]
            info.genre_tags_cn = best[3]

            # 次要曲风（分数 ≥ 30 且非排名第一）
            for p_en, p_cn, t_en, t_cn, score in scored[1:]:
                if score >= 30:
                    info.secondary_genres.append({
                        "en": p_en, "cn": p_cn,
                        "tags_en": t_en, "tags_cn": t_cn,
                        "confidence": round(score / 100.0, 3),
                    })

            # 地域风格推断
            regional_hints = {
                "Chinese Traditional / Guofeng": "东方/中国",
                "J-Pop / Japanese-style": "日本",
                "K-Pop": "韩国",
                "Arabic / Middle Eastern": "中东/阿拉伯",
                "Indian / Bollywood": "南亚/印度",
                "Latin": "拉丁美洲",
            }
            info.regional_style = regional_hints.get(best[0], "")
            if not info.regional_style:
                info.regional_style = "国际/欧美 (International/Western)"

            # 文化影响描述
            culture_map = {
                "Chinese Traditional / Guofeng": "中国五声音阶、民族乐器与古风诗词意境",
                "J-Pop / Japanese-style": "日式旋律写作、City Pop 和声、动漫配器风格",
                "K-Pop": "韩国流行制作工艺、强节奏驱动、多段式编曲",
                "Arabic / Middle Eastern": "阿拉伯微分音程、中东打击节奏与弦乐",
                "Indian / Bollywood": "印度拉格音阶、塔布拉打击乐、宝莱坞编曲",
                "Latin": "拉丁打击律动、切分贝斯线、铜管编配",
                "Campus Folk / School Ballad": "校园吉他弹唱、朴素编曲、青春怀旧气息",
                "Pop": "主流流行编排、旋律驱动、商业化制作",
                "Rock": "电吉他驱动、强节拍、主唱突出",
                "Electronic / EDM": "合成器主导、电子鼓机、舞曲结构",
                "Jazz": "即兴感、复杂和声、摇摆律动",
                "Classical": "管弦编制、传统和声、动态对比",
                "Folk": "原声吉他、叙事性歌词、民间根源",
                "R&B / Soul": "蓝调音阶、灵魂唱腔、舒缓律动",
                "Hip-Hop / Rap": "节拍驱动、说唱韵律、采样文化",
                "Metal": "失真吉他、双踩鼓、侵略性能量",
                "Blues": "蓝调音阶、shuffle节奏、情感表达",
                "Reggae": "反拍节奏、贝斯驱动、慵懒律动",
                "Funk": "切分律动、贝斯主导、铜管点缀",
                "New Age": "电子氛围、冥想感、空灵音色",
                "Ambient": "环境音景、极简结构、空间感",
                "Cinematic / Orchestral": "电影感、史诗叙事、管弦乐编制",
                "Dance": "舞曲节拍、电子音色、能量驱动",
                "Indie / Alternative": "独立制作、非主流审美、实验精神",
                "Bossa Nova": "巴西节奏、吉他扫弦、轻柔氛围",
                "Children's / Nursery": "简单旋律、重复结构、轻松愉快",
            }
            info.cultural_influence = culture_map.get(best[0], "综合多元音乐元素")

        self.genre = info
        return info

    # ---- 完整分析 ----

    def run_full_analysis(self) -> SongAnalysis:
        steps = [
            ("基础信息", self.analyze_basic),
            ("BPM/速度", self.analyze_tempo),
            ("调式分析", self.analyze_key),
            ("节奏特征", self.analyze_rhythm),
            ("频谱特征", self.analyze_spectral),
            ("能量动态", self.analyze_energy),
            ("乐器推测", self.analyze_instruments),
            ("人声特征", self.analyze_vocals),
            ("曲式结构", self.analyze_structure),
            ("情绪氛围", self.analyze_mood),
            ("和声走向", self.analyze_harmony),
            ("制作特征", self.analyze_production),
            ("曲风归类", self.analyze_genre),
        ]
        for name, fn in steps:
            print(f"  [{name}] ...", end=" ", flush=True)
            try:
                fn()
                print("✓")
            except Exception as e:
                print(f"✗ ({e})")
        return SongAnalysis(
            basic=self.basic, tempo=self.tempo, key=self.key,
            rhythm=self.rhythm, spectral=self.spectral, energy=self.energy,
            instruments=self.instruments, vocals=self.vocals,
            structure=self.structure, mood=self.mood, harmony=self.harmony,
            production=self.production, genre=self.genre,
        )


# ============================================================
# Suno 提示词生成
# ============================================================

def generate_suno_prompt(analysis: SongAnalysis) -> dict:
    """基于分析结果生成 Suno AI 风格提示词 (双语)。"""
    a = analysis
    tags_en = []
    tags_cn = []

    # Genre/Style from tempo + mode
    bpm = a.tempo.bpm
    is_major = a.key.detected_mode == "major"

    if bpm > 120:
        tags_en.append("upbeat"); tags_cn.append("快节奏")
    elif bpm > 90:
        tags_en.append("mid-tempo"); tags_cn.append("中速")
    else:
        tags_en.append("slow tempo"); tags_cn.append("慢速")

    tags_en.append(a.tempo.tempo_class.lower() if a.tempo.tempo_class else "moderate")
    tags_cn.append(a.tempo.tempo_class_cn or "中速")

    if is_major:
        tags_en.append("major key"); tags_cn.append("大调")
    else:
        tags_en.append("minor key"); tags_cn.append("小调")

    # Genre from genre analysis (priority placement)
    genre_en_tags = a.genre.genre_tags_en if a.genre.genre_tags_en else []
    genre_cn_tags = a.genre.genre_tags_cn if a.genre.genre_tags_cn else []
    tags_en = genre_en_tags + tags_en
    tags_cn = genre_cn_tags + tags_cn

    # Instruments
    inst_en = []
    inst_cn = []
    for inst in a.instruments.likely_instruments:
        parts = inst.split(" (")
        cn_part = parts[0]
        en_part = parts[1].rstrip(")") if len(parts) > 1 else parts[0]
        inst_cn.append(cn_part)
        inst_en.append(en_part)

    # Vocals
    vocal_en = ""
    vocal_cn = ""
    if a.vocals.has_vocals:
        if "男" in a.vocals.vocal_gender:
            vocal_en = "male vocals"; vocal_cn = "男声"
        elif "女" in a.vocals.vocal_gender:
            vocal_en = "female vocals"; vocal_cn = "女声"
        else:
            vocal_en = "vocals"; vocal_cn = "人声"
        if a.vocals.is_rap:
            vocal_en += ", rap style"; vocal_cn += ", 说唱风格"
    else:
        vocal_en = "instrumental"; vocal_cn = "纯器乐"

    # Mood
    mood_en = a.mood.primary_emotion_en or "neutral"
    mood_cn = a.mood.primary_emotion_cn or "中性"

    # Build structured tags
    structured_tags = {
        "genre": a.genre.primary_genre_en,
        "genre_cn": a.genre.primary_genre_cn,
        "genre_confidence": a.genre.genre_confidence,
        "secondary_genres": a.genre.secondary_genres,
        "style": tags_en,
        "bpm": bpm,
        "key": f"{a.key.detected_key} {a.key.detected_mode}",
        "instruments": inst_en,
        "vocals": vocal_en,
        "mood": mood_en,
        "timbre": a.spectral.timbre_description,
        "energy": a.energy.compression_feel,
        "reverb": a.production.reverb_level,
    }

    # ================================================================
    # 构建综合自然语言提示词（中英双语，覆盖全部 13 维度关键要素）
    # ================================================================

    # --- 提前构建和声数据（后续多处引用）---
    harmony_notes = {}
    if a.harmony.estimated_progression:
        tonic_note = a.key.detected_key or "C"
        try:
            tonic_semi = NOTE_NAMES_SHARP.index(tonic_note)
        except ValueError:
            tonic_semi = 0
        mode = a.key.detected_mode or "major"
        prog = a.harmony.estimated_progression
        actual_chords = " → ".join(
            _roman_to_chord_name(r, tonic_semi, mode) for r in prog.split("-")
        )
        harmony_notes = {
            "progression_roman": prog,
            "progression_actual": actual_chords,
            "confidence": a.harmony.progression_confidence,
            "complexity": a.harmony.chord_complexity,
            "style_hint": "",
        }
        for prog_str, cn_name, style_label, _ in COMMON_PROGRESSIONS:
            if prog_str == prog:
                harmony_notes["style_hint"] = style_label
                harmony_notes["progression_cn"] = cn_name
                break

    # --- 英文音色描述 ---
    timbre_en = "dark" if "暗" in a.spectral.timbre_description else (
        "warm" if "温暖" in a.spectral.timbre_description else (
            "bright" if "明亮" in a.spectral.timbre_description else "sharp"
        )
    )

    # --- 人声风格细节 ---
    vocal_style_en = ""
    vocal_style_cn = ""
    if a.vocals.has_vocals and a.vocals.vocal_style_hints:
        # 从双语字符串中提取纯中文 / 纯英文部分
        en_hints_list = []
        cn_hints_list = []
        for hint in a.vocals.vocal_style_hints:
            if " (" in hint:
                en_part, rest = hint.split(" (", 1)
                cn_part = rest.rstrip(")")
                en_hints_list.append(en_part.strip())
                cn_hints_list.append(cn_part.strip())
            else:
                en_hints_list.append(hint)
                cn_hints_list.append(hint)
        vocal_style_cn = "、".join(cn_hints_list)
        vocal_style_en = ", ".join(en_hints_list)

    # --- 和声走向 ---
    harmony_cn_text = ""
    harmony_en_text = ""
    if harmony_notes.get("progression_roman"):
        harm_prog = harmony_notes["progression_roman"]
        harm_actual = harmony_notes.get("progression_actual", "")
        harmony_cn_text = f"和弦进行 {harm_prog}"
        harmony_en_text = f"Chord progression {harm_prog}"
        if harm_actual:
            harmony_cn_text += f" ({harm_actual})"
            harmony_en_text += f" ({harm_actual})"

    # --- 节奏特征 ---
    rhythm_cn_parts = []
    rhythm_en_parts = []
    if a.rhythm.rhythm_complexity_cn:
        # 字段值已含完整描述，直接使用
        rhythm_cn_parts.append(f"节奏{a.rhythm.rhythm_complexity_cn}")
        rhythm_en_parts.append(f"{a.rhythm.rhythm_complexity} rhythm")
    if a.rhythm.syncopation_level_cn and a.rhythm.syncopation_level not in ("none", ""):
        rhythm_cn_parts.append(f"切分{a.rhythm.syncopation_level_cn}")
        rhythm_en_parts.append(f"{a.rhythm.syncopation_level.lower()} syncopation")
    if a.rhythm.swing_ratio > 0.3:
        rhythm_cn_parts.append("摇摆感较强")
        rhythm_en_parts.append("swing feel")
    rhythm_cn_text = "；".join(rhythm_cn_parts) if rhythm_cn_parts else ""
    rhythm_en_text = ", ".join(rhythm_en_parts) if rhythm_en_parts else ""

    # --- 曲式结构（加入间奏推断 + 完整 Suno 风格描述）---
    struct_parts_cn = []
    struct_parts_en = []
    if a.structure.has_intro:
        struct_parts_cn.append("前奏")
        struct_parts_en.append("intro")
    # 主副歌是基础结构
    struct_parts_cn.append("主歌 → 副歌")
    struct_parts_en.append("verse → chorus")
    if a.structure.chorus_count and a.structure.chorus_count >= 2:
        struct_parts_cn.append("主歌 → 副歌")
        struct_parts_en.append("verse → chorus")
    if a.structure.has_bridge:
        struct_parts_cn.append("桥段 → 副歌")
        struct_parts_en.append("bridge → chorus")
    # 间奏推断：段落数充裕且未检测到前奏尾奏桥段时，推断间奏
    if a.structure.num_segments >= 5:
        struct_parts_cn.append("间奏")
        struct_parts_en.append("interlude")
    if a.structure.has_outro:
        struct_parts_cn.append("尾奏")
        struct_parts_en.append("outro")
    struct_flow_cn = " → ".join(struct_parts_cn) if struct_parts_cn else "主歌 → 副歌"
    struct_flow_en = " → ".join(struct_parts_en) if struct_parts_en else "verse → chorus"
    struct_cn_text = f"曲式结构：{struct_flow_cn}"
    struct_en_text = f"structure: {struct_flow_en}"

    # --- 制作特征（字段值已含完整描述，直接沿用）---
    prod_cn_parts = []
    prod_en_parts = []
    if a.production.reverb_level_cn and "未检测" not in a.production.reverb_level_cn:
        prod_cn_parts.append(a.production.reverb_level_cn)
        prod_en_parts.append(f"{a.production.reverb_level} reverb")
    if a.production.stereo_width_cn and "未检测" not in a.production.stereo_width_cn:
        prod_cn_parts.append(a.production.stereo_width_cn)
        prod_en_parts.append(f"{a.production.stereo_width} stereo")
    if a.energy.compression_feel_cn:
        prod_cn_parts.append(a.energy.compression_feel_cn)
        prod_en_parts.append(f"{a.energy.compression_feel} dynamics")
    prod_cn_text = "，".join(prod_cn_parts) if prod_cn_parts else ""
    prod_en_text = ", ".join(prod_en_parts) if prod_en_parts else ""

    # --- 地域风格 ---
    regional_text_cn = ""
    regional_text_en = ""
    if a.genre.regional_style and a.genre.regional_style != "无特定地域":
        regional_text_cn = f"地域风格：{a.genre.regional_style}"
        regional_text_en = f"regional style: {a.genre.regional_style}"

    # ======== 英文自然语言提示词 (按 Suno 风格逻辑排序) ========
    # Suno 推荐顺序: Genre → Key → BPM → Instruments → Vocals → Mood → Timbre
    #                → Harmony → Rhythm → Structure → Production → Regional
    prompt_en_parts = [
        f"A {', '.join(tags_en)} song",
        f"in {a.key.detected_key} {a.key.detected_mode}",
        f"at {bpm} BPM ({a.tempo.tempo_class})",
    ]
    if inst_en:
        prompt_en_parts.append(f"featuring {', '.join(inst_en)}")
    if vocal_en:
        vocal_en_full = vocal_en
        if vocal_style_en:
            vocal_en_full += f" ({vocal_style_en})"
        prompt_en_parts.append(f"with {vocal_en_full}")
    prompt_en_parts.append(f"{mood_en} mood")
    prompt_en_parts.append(f"{timbre_en} timbre")
    # 和声走向
    if harmony_en_text:
        prompt_en_parts.append(harmony_en_text)
    # 节奏
    if rhythm_en_text:
        prompt_en_parts.append(rhythm_en_text)
    # 曲式结构（含前奏/间奏/尾奏）
    if struct_en_text:
        prompt_en_parts.append(struct_en_text)
    # 制作特征
    if prod_en_text:
        prompt_en_parts.append(prod_en_text)
    # 地域风格
    if regional_text_en:
        prompt_en_parts.append(regional_text_en)
    prompt_en = ", ".join(prompt_en_parts) + "."

    # ======== 中文自然语言提示词 (按 Suno 风格逻辑排序) ========
    # Suno 推荐顺序: 曲风 → 调式 → 速度 → 配器 → 人声 → 情绪 → 音色
    #                → 和声 → 节奏 → 曲式结构 → 制作 → 地域
    prompt_cn_parts = [
        f"一首{', '.join(tags_cn)}歌曲",
        f"{a.key.detected_key}{'大调' if is_major else '小调'}",
        f"速度{bpm} BPM ({a.tempo.tempo_class_cn})",
    ]
    if inst_cn:
        prompt_cn_parts.append(f"配器包括{'、'.join(inst_cn)}")
    if vocal_cn:
        vocal_cn_full = vocal_cn
        if vocal_style_cn:
            vocal_cn_full += f"（{vocal_style_cn}）"
        prompt_cn_parts.append(f"采用{vocal_cn_full}")
    prompt_cn_parts.append(f"情绪{mood_cn}")
    prompt_cn_parts.append(f"音色{a.spectral.timbre_description}")
    # 和声走向
    if harmony_cn_text:
        prompt_cn_parts.append(harmony_cn_text)
    # 节奏
    if rhythm_cn_text:
        prompt_cn_parts.append(rhythm_cn_text)
    # 曲式结构（含前奏/间奏/尾奏）
    if struct_cn_text:
        prompt_cn_parts.append(struct_cn_text)
    # 制作特征
    if prod_cn_text:
        prompt_cn_parts.append(prod_cn_text)
    # 地域风格
    if regional_text_cn:
        prompt_cn_parts.append(regional_text_cn)
    prompt_cn = "，".join(prompt_cn_parts) + "。"

    # Production notes
    prod_notes = []
    if a.production.reverb_level_cn:
        prod_notes.append(f"混响: {a.production.reverb_level_cn}")
    if a.production.stereo_width_cn:
        prod_notes.append(f"声场: {a.production.stereo_width_cn}")
    if a.production.production_era_cn:
        prod_notes.append(f"制作: {a.production.production_era_cn}")
    if a.energy.compression_feel_cn:
        prod_notes.append(f"动态: {a.energy.compression_feel_cn}")

    # Rhythm notes
    rhythm_notes = []
    if a.rhythm.rhythm_complexity_cn:
        rhythm_notes.append(f"节奏: {a.rhythm.rhythm_complexity_cn}")
    if a.rhythm.syncopation_level_cn:
        rhythm_notes.append(f"切分: {a.rhythm.syncopation_level_cn}")

    # Suno V4 风格标签，仅含有效字段
    v4_tags_parts = []
    if genre_en_tags:
        v4_tags_parts.append(f"[Genre: {', '.join(genre_en_tags)}]")
    v4_tags_parts.append(f"[Key: {a.key.detected_key} {a.key.detected_mode}]")
    v4_tags_parts.append(f"[BPM: {bpm}]")
    if inst_en:
        v4_tags_parts.append(f"[Instruments: {', '.join(inst_en)}]")
    if vocal_en:
        v4_tags_parts.append(f"[Vocals: {vocal_en}]")
    if mood_en and mood_en != "neutral":
        v4_tags_parts.append(f"[Mood: {mood_en}]")
    if a.energy.compression_feel or a.production.reverb_level:
        v4_tags_parts.append(f"[Production: {a.energy.compression_feel}, {a.production.reverb_level}]")
    # 曲式结构标签（含前奏/间奏/尾奏）
    if struct_en_text:
        v4_tags_parts.append(f"[Structure: {struct_flow_en}]")

    return {
        "structured_tags": structured_tags,
        "suno_prompt_en": prompt_en,
        "suno_prompt_cn": prompt_cn,
        "production_notes": prod_notes,
        "rhythm_notes": rhythm_notes,
        "harmony_notes": harmony_notes,
        "vocal_style_hints": a.vocals.vocal_style_hints,
        "structure": {
            "sections": a.structure.num_segments,
            "has_intro": a.structure.has_intro,
            "has_outro": a.structure.has_outro,
            "has_bridge": a.structure.has_bridge,
            "chorus_count": a.structure.chorus_count,
        },
        "style_tags_complete": {
            "genre_keywords_en": tags_en,
            "genre_keywords_cn": tags_cn,
            "instrument_keywords_en": inst_en,
            "instrument_keywords_cn": inst_cn,
        },
        "sunov4_style_prompt": "".join(v4_tags_parts),
    }


# ============================================================
# 纯文本报告生成
# ============================================================

def generate_text_report(analysis: SongAnalysis, suno: dict, original_filename: str) -> str:
    """生成独立、完整的纯文本歌曲分析报告（不与其他歌曲比较）。"""
    from datetime import datetime
    a = analysis
    bpm = a.tempo.bpm
    duration = a.basic.duration_sec
    mins = int(duration // 60)
    secs = int(duration % 60)
    dur_str = f"{mins}分{secs}秒" if duration > 0 else "未能获取"

    channels_desc = f"{a.basic.channels}声道"
    channels_desc += " (立体声)" if a.basic.channels >= 2 else " (单声道)"

    # 乐器列表
    inst_rows = []
    for inst in a.instruments.likely_instruments:
        inst_rows.append(f"    ✓ {inst}")

    # 人声风格
    vocal_rows = []
    for vs in a.vocals.vocal_style_hints:
        vocal_rows.append(f"    · {vs}")

    # 备选调式
    alt_key_rows = []
    for ak in a.key.alternative_keys:
        cn_mode = "大调" if ak["mode"] == "major" else "小调"
        alt_key_rows.append(f"    · {ak['key']}{cn_mode} (置信度 {int(ak['confidence']*100)}%)")

    # 检测到的和弦
    chord_rows = []
    for c in a.harmony.detected_chords:
        chord_rows.append(f"    · {c['name']} (置信度 {int(c['confidence']*100)}%)")

    # 和弦进行式
    harmony_detail = ""
    if a.harmony.estimated_progression:
        tonic_note = a.key.detected_key or "C"
        try:
            tonic_semi = NOTE_NAMES_SHARP.index(tonic_note)
        except ValueError:
            tonic_semi = 0
        mode = a.key.detected_mode or "major"
        prog = a.harmony.estimated_progression
        actual = " → ".join(
            _roman_to_chord_name(r, tonic_semi, mode) for r in prog.split("-")
        )
        # 找中文名
        prog_cn = ""
        for p, cn, _, _ in COMMON_PROGRESSIONS:
            if p == prog:
                prog_cn = f" ({cn})"
                break
        harmony_detail = (
            f"  推测进行式    {prog}{prog_cn}\n"
            f"  置信度        {int(a.harmony.progression_confidence * 100)}%\n"
            f"  在此调下的和弦  {actual}\n"
            f"  和弦复杂度    {a.harmony.chord_complexity}"
        )
    else:
        harmony_detail = "  未能检测到明确的和弦进行式"

    # Suno 提示词
    v4_tags = suno.get("sunov4_style_prompt", "")
    prompt_en = suno.get("suno_prompt_en", "")
    prompt_cn = suno.get("suno_prompt_cn", "")

    # 构建可直接复制到 Suno 的综合提示词
    harmony_notes = suno.get("harmony_notes", {})
    harmony_suno_tag = ""
    if harmony_notes.get("progression_roman"):
        harmony_suno_tag = f"[Chords: {harmony_notes['progression_roman']} ({harmony_notes.get('progression_actual', '')})]"
    production_section = f"[Production: {a.energy.compression_feel}, {a.production.reverb_level}]"

    suno_copy_block = (
        f"{v4_tags}\n"
        f"{harmony_suno_tag}\n"
        f"{production_section}\n"
        f"\n"
        f"# English prompt:\n"
        f"{prompt_en}\n"
        f"\n"
        f"# 中文提示词:\n"
        f"{prompt_cn}"
    )

    report = f"""\
{'='*70}
  音乐分析报告
  曲目：{original_filename}
  生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{'='*70}

┌─────────────────────────────────────────────────────────────┐
│ 一、基础信息                                                │
├─────────────────────────────────────────────────────────────┤
  格式          {a.basic.format_name or '无法获取'}
  时长          {dur_str} ({int(duration)}秒)
  采样率        {a.basic.sample_rate} Hz
  比特率        {a.basic.bitrate_kbps} kbps
  声道          {channels_desc}
  编码          {a.basic.codec or '无法获取'}

┌─────────────────────────────────────────────────────────────┐
│ 二、BPM / 速度                                              │
├─────────────────────────────────────────────────────────────┤
  BPM           {bpm}
  置信度        {int(a.tempo.bpm_confidence * 100)}%
  速度分类      {a.tempo.tempo_class_cn} ({a.tempo.tempo_class})
  节拍强度      {'较强' if a.tempo.beat_strength > 0.3 else '中等' if a.tempo.beat_strength > 0.1 else '较弱'} ({a.tempo.beat_strength:.2f})

┌─────────────────────────────────────────────────────────────┐
│ 三、调式分析                                                │
├─────────────────────────────────────────────────────────────┤
  主调式        {a.key.detected_key} {'大调 (major)' if a.key.detected_mode=='major' else '小调 (minor)'}
  置信度        {int(a.key.confidence * 100)}%{' — 高' if a.key.confidence > 0.7 else ' — 中等' if a.key.confidence > 0.4 else ' — 低（仅供参考）'}
  备选调式      {f"共{len(alt_key_rows)}个备选" if alt_key_rows else "无"}
{chr(10).join(alt_key_rows) if alt_key_rows else ''}
  {'说明：大调通常带来明亮、积极的听感' if a.key.detected_mode=='major' else '说明：小调通常带来忧伤、深沉或戏剧性的听感'}

┌─────────────────────────────────────────────────────────────┐
│ 四、节奏特征                                                │
├─────────────────────────────────────────────────────────────┤
  拍号          {a.rhythm.time_signature} 拍
  节奏复杂度    {a.rhythm.rhythm_complexity_cn or '未检测'}
  切分音        {a.rhythm.syncopation_level_cn or '未检测'}
  摇摆感        {f'较强 ({a.rhythm.swing_ratio:.2f})' if a.rhythm.swing_ratio > 0.5 else f'较弱 ({a.rhythm.swing_ratio:.2f})' if a.rhythm.swing_ratio > 0 else '未检测'}

┌─────────────────────────────────────────────────────────────┐
│ 五、频谱音色                                                │
├─────────────────────────────────────────────────────────────┤
  频谱质心      {a.spectral.centroid_mean} Hz
  音色描述      {a.spectral.timbre_description}
  频谱带宽      {a.spectral.bandwidth_mean} Hz
  滚降频率      {a.spectral.rolloff_mean} Hz

┌─────────────────────────────────────────────────────────────┐
│ 六、能量动态                                                │
├─────────────────────────────────────────────────────────────┤
  平均能量      {a.energy.rms_mean:.4f}
  峰值能量      {a.energy.rms_max:.4f}
  动态范围      {a.energy.dynamic_range_db} dB
  压缩程度      {a.energy.compression_feel_cn}

┌─────────────────────────────────────────────────────────────┐
│ 七、乐器推测                                                │
├─────────────────────────────────────────────────────────────┤
  和声能量占比  {a.instruments.harmonic_energy_ratio * 100:.1f}%
  打击乐占比    {a.instruments.percussive_energy_ratio * 100:.1f}%
  推测乐器      {f"共{len(a.instruments.likely_instruments)}种" if a.instruments.likely_instruments else '未检测到'}
{chr(10).join(inst_rows) if inst_rows else '    无'}

┌─────────────────────────────────────────────────────────────┐
│ 八、人声特征                                                │
├─────────────────────────────────────────────────────────────┤
  是否有人声    {'有' if a.vocals.has_vocals else '无（纯器乐）'}
  声音倾向      {a.vocals.vocal_gender or '-'}
  音域          {a.vocals.vocal_range_approx or '-'}
  人声能量比    {f"{a.vocals.vocal_energy_ratio * 100:.1f}%" if a.vocals.has_vocals else '-'}
  人声风格      {f"共{len(a.vocals.vocal_style_hints)}个特征" if a.vocals.vocal_style_hints else '-'}
{chr(10).join(vocal_rows) if vocal_rows else ''}
  是否Rap       {'是' if a.vocals.is_rap else '否'}

┌─────────────────────────────────────────────────────────────┐
│ 九、情绪氛围                                                │
├─────────────────────────────────────────────────────────────┤
  主要情绪      {a.mood.primary_emotion_cn} ({a.mood.primary_emotion_en})
  效价(愉悦度)  {a.mood.valence_estimate}
  唤醒度        {a.mood.arousal_estimate}

┌─────────────────────────────────────────────────────────────┐
│ 十、和声走向（和弦进行）                                    │
├─────────────────────────────────────────────────────────────┤
{harmony_detail}
  检测到的和弦：
{chr(10).join(chord_rows) if chord_rows else '    未能识别明确和弦'}

┌─────────────────────────────────────────────────────────────┐
│ 十一、制作特征                                              │
├─────────────────────────────────────────────────────────────┤
  混响程度      {a.production.reverb_level_cn}
  立体声宽度    {a.production.stereo_width_cn}
  响度          {f'约 {a.production.loudness_lufs} LUFS' if a.production.loudness_lufs > -900 else '无法评估'}
  制作年代感    {a.production.production_era_cn}
  混音质量      {a.production.mix_quality}

┌─────────────────────────────────────────────────────────────┐
│ 十二、曲风归类                                               │
├─────────────────────────────────────────────────────────────┤
  主曲风        {a.genre.primary_genre_cn} ({a.genre.primary_genre_en})
  置信度        {int(a.genre.genre_confidence * 100)}%
  曲风标签      {', '.join(a.genre.genre_tags_cn)}
  地域风格      {a.genre.regional_style}
  文化影响      {a.genre.cultural_influence}
  次要曲风      {f"共{len(a.genre.secondary_genres)}种" if a.genre.secondary_genres else '无'}
{chr(10).join(f'    · {sg["cn"]} ({sg["en"]}) — 置信度 {int(sg["confidence"]*100)}%' for sg in a.genre.secondary_genres) if a.genre.secondary_genres else ''}


{'='*70}
  ★ 可直接复制到 Suno AI 的综合提示词
{'='*70}

{suno_copy_block}

{'='*70}
使用说明：
  1. 选中上方「★」标记到本行之间的全部文本
  2. 复制 (Ctrl+C)
  3. 打开 Suno AI，将标签式提示词粘贴到「Style Prompt」框
  4. 将英文/中文自然语言提示词粘贴到「Lyrics」或「Description」框
  5. 点击生成，即可获得与原曲风格高度相似的歌曲
{'='*70}
"""
    return report


# ============================================================
# HTML 静态报告生成
# ============================================================

def generate_html_report(analysis: SongAnalysis, suno: dict, original_filename: str) -> str:
    """生成醒目的单文件静态 HTML 歌曲分析报告。"""
    a = analysis
    from datetime import datetime

    bpm = a.tempo.bpm
    duration = a.basic.duration_sec
    mins = int(duration // 60) if duration > 0 else 0
    secs = int(duration % 60) if duration > 0 else 0
    dur_str = f"{mins}:{secs:02d}"

    # 乐器列表
    inst_rows = ""
    for inst in a.instruments.likely_instruments:
        inst_rows += f'<li>{inst}</li>\n'

    # 人声风格
    vocal_rows = ""
    for vs in a.vocals.vocal_style_hints:
        vocal_rows += f'<li>{vs}</li>\n'

    # 备选调式
    alt_key_rows = ""
    for ak in a.key.alternative_keys:
        cn_mode = "大调" if ak["mode"] == "major" else "小调"
        alt_key_rows += f'<li>{ak["key"]}{cn_mode} — 置信度 {int(ak["confidence"]*100)}%</li>\n'

    # 检测到的和弦
    chord_rows = ""
    for c in a.harmony.detected_chords:
        chord_rows += f'<li>{c["name"]} <small>({int(c["confidence"]*100)}%)</small></li>\n'

    # 和弦进行式
    harmony_detail = ""
    if a.harmony.estimated_progression:
        tonic_note = a.key.detected_key or "C"
        try:
            tonic_semi = NOTE_NAMES_SHARP.index(tonic_note)
        except ValueError:
            tonic_semi = 0
        mode = a.key.detected_mode or "major"
        prog = a.harmony.estimated_progression
        actual = " → ".join(
            _roman_to_chord_name(r, tonic_semi, mode) for r in prog.split("-")
        )
        prog_cn = ""
        for p, cn, _, _ in COMMON_PROGRESSIONS:
            if p == prog:
                prog_cn = f" ({cn})"
                break
        harmony_detail = f"""
        <div class="harmony-card">
          <div class="harmony-progression">{prog}{prog_cn}</div>
          <div class="harmony-actual">在此调下的和弦: <strong>{actual}</strong></div>
          <div class="harmony-meta">
            <span>置信度: {int(a.harmony.progression_confidence * 100)}%</span>
            <span>复杂度: {a.harmony.chord_complexity}</span>
          </div>
        </div>
        """
    else:
        harmony_detail = '<p class="text-muted">未能检测到明确的和弦进行式</p>'

    # Suno 提示词
    v4_tags = suno.get("sunov4_style_prompt", "")
    prompt_en = suno.get("suno_prompt_en", "")
    prompt_cn = suno.get("suno_prompt_cn", "")
    harmony_suno = suno.get("harmony_notes", {})
    harmony_tag = ""
    if harmony_suno.get("progression_roman"):
        harmony_tag = f'[Chords: {harmony_suno["progression_roman"]} ({harmony_suno.get("progression_actual", "")})]'

    production_tag = f"[Production: {a.energy.compression_feel}, {a.production.reverb_level}]"

    # 标签头部（V4 标签已内含 Production，额外仅加 Chords）
    suno_tags_top = f"""{v4_tags}
{harmony_tag}"""

    # 单独的英文/中文区块（用于独立复制按钮，JS 复制时自动去掉标签前缀）
    en_block = f"[English]\n{prompt_en}"
    cn_block = f"[中文]\n{prompt_cn}"

    # 预计算动态标签 HTML 片段（避免嵌套 f-string 引号冲突）
    vocal_tags_html = ""
    if a.vocals.vocal_style_hints:
        vocal_tags_html = '<div class="tag-list" style="margin-top:12px">' + \
            ' '.join(f'<span class="tag">{vs}</span>' for vs in a.vocals.vocal_style_hints) + '</div>'

    mood_tags_html = ""
    if a.mood.secondary_emotions:
        mood_tags_html = '<div class="tag-list" style="margin-top:12px">' + \
            ' '.join(f'<span class="tag">{e["cn"]} ({e["en"]})</span>' for e in a.mood.secondary_emotions) + '</div>'

    chord_tags_html = ""
    if a.harmony.detected_chords:
        chord_tags_html = '<div class="tag-list" style="margin-top:12px">' + \
            ' '.join(f'<span class="tag">{c["name"]}</span>' for c in a.harmony.detected_chords) + '</div>'

    # 次要曲风标签 HTML（用于第十二维度区段）
    secondary_genre_html = ""
    if a.genre.secondary_genres:
        parts = []
        for sg in a.genre.secondary_genres:
            parts.append(f'<span class="tag tag-secondary">{sg["cn"]} ({int(sg["confidence"]*100)}%)</span>')
        secondary_genre_html = '<div class="tag-list" style="margin-top:8px"><span class="text-muted">次要曲风: </span>' + \
            ' '.join(parts) + '</div>'

    # 次要曲风标签（用于曲风亮点卡片）
    secondary_genre_rows = ""
    for sg in a.genre.secondary_genres:
        secondary_genre_rows += f'<span class="genre-tag secondary">{sg["cn"]} ({int(sg["confidence"]*100)}%)</span>\n'

    # 曲风进度条函数
    def _confidence_bar(val, label):
        pct = int(val * 100)
        color = "#4CAF50" if pct >= 70 else ("#FF9800" if pct >= 40 else "#f44336")
        return f'''<div class="confidence-bar">
      <span class="bar-label">{label}</span>
      <div class="bar-track"><div class="bar-fill" style="width:{pct}%;background:{color}"></div></div>
      <span class="bar-value">{pct}%</span></div>'''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>歌曲分析报告 — {original_filename}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    background: #f0f2f5; color: #1a1a2e; line-height: 1.7;
    padding: 20px;
  }}
  .container {{ max-width: 1000px; margin: 0 auto; }}
  /* 头部 */
  .header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    color: #fff; border-radius: 16px; padding: 40px 36px; margin-bottom: 24px;
    box-shadow: 0 8px 32px rgba(15, 52, 96, 0.25);
  }}
  .header h1 {{ font-size: 28px; font-weight: 700; letter-spacing: 0.5px; }}
  .header .subtitle {{ font-size: 14px; opacity: 0.7; margin-top: 6px; }}
  .header .meta {{ display: flex; gap: 24px; margin-top: 16px; flex-wrap: wrap; }}
  .header .meta-item {{ display: flex; align-items: center; gap: 6px; font-size: 13px; opacity: 0.85; }}

  /* 概览卡片 */
  .overview {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px; margin-bottom: 24px;
  }}
  .overview-card {{
    background: #fff; border-radius: 12px; padding: 18px; text-align: center;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06); transition: transform 0.2s;
  }}
  .overview-card:hover {{ transform: translateY(-2px); }}
  .overview-card .label {{ font-size: 11px; color: #8b8fa3; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; }}
  .overview-card .value {{ font-size: 20px; font-weight: 700; color: #1a1a2e; }}
  .overview-card .sub {{ font-size: 12px; color: #8b8fa3; margin-top: 2px; }}

  /* 维度分区 */
  .section {{
    background: #fff; border-radius: 14px; padding: 28px; margin-bottom: 20px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.05);
  }}
  .section-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }}
  .section-num {{
    width: 36px; height: 36px; border-radius: 10px; display: flex;
    align-items: center; justify-content: center; font-weight: 700; font-size: 15px;
    color: #fff; flex-shrink: 0;
  }}
  .section-title {{ font-size: 18px; font-weight: 700; }}
  .section-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }}
  .section-item {{
    background: #f8f9fc; border-radius: 10px; padding: 14px 16px;
    border-left: 3px solid #e0e4f0;
  }}
  .section-item .key {{ font-size: 12px; color: #8b8fa3; margin-bottom: 2px; }}
  .section-item .val {{ font-size: 15px; font-weight: 600; color: #1a1a2e; }}

  /* 颜色主题 */
  .c1 {{ background: #667eea; }}  .c2 {{ background: #f093fb; }}
  .c3 {{ background: #4facfe; }}  .c4 {{ background: #43e97b; }}
  .c5 {{ background: #fa709a; }}  .c6 {{ background: #feca57; }}
  .c7 {{ background: #ff6b6b; }}  .c8 {{ background: #a29bfe; }}
  .c9 {{ background: #00b894; }}  .c10 {{ background: #e17055; }}
  .c11 {{ background: #0984e3; }} .c12 {{ background: #6c5ce7; }}
  .c13 {{ background: #d63031; }}

  /* 曲风亮点卡片 */
  .genre-hero {{
    background: linear-gradient(135deg, #fff5f5 0%, #fef3e2 50%, #fff9e6 100%);
    border: 2px solid #feca57; border-radius: 14px; padding: 28px; margin-bottom: 20px;
    text-align: center; box-shadow: 0 4px 20px rgba(254, 202, 87, 0.15);
  }}
  .genre-hero .primary-genre {{
    font-size: 32px; font-weight: 800; color: #d63031; margin-bottom: 4px;
  }}
  .genre-hero .genre-en {{ font-size: 16px; color: #636e72; }}
  .genre-hero .genre-meta {{ display: flex; justify-content: center; gap: 20px; margin-top: 12px; flex-wrap: wrap; }}
  .genre-tag {{
    display: inline-block; padding: 4px 14px; border-radius: 20px;
    font-size: 12px; font-weight: 600; margin: 2px;
  }}
  .genre-tag.primary {{ background: #d63031; color: #fff; }}
  .genre-tag.secondary {{ background: #feca57; color: #1a1a2e; }}

  /* 和弦卡片 */
  .harmony-card {{
    background: linear-gradient(135deg, #e8f0fe, #f3e8ff);
    border-radius: 12px; padding: 20px; text-align: center; margin-top: 8px;
  }}
  .harmony-progression {{
    font-size: 28px; font-weight: 800; font-family: "Courier New", monospace;
    color: #6c5ce7; letter-spacing: 2px;
  }}
  .harmony-actual {{ font-size: 16px; color: #636e72; margin-top: 6px; }}
  .harmony-meta {{ display: flex; justify-content: center; gap: 16px; font-size: 13px; color: #8b8fa3; margin-top: 8px; }}

  /* Suno 提示词块 */
  .suno-block {{
    background: linear-gradient(135deg, #1a1a2e, #16213e);
    color: #e0e4f0; border-radius: 14px; padding: 28px; margin-bottom: 20px;
    box-shadow: 0 8px 32px rgba(15, 52, 96, 0.3);
    position: relative;
  }}
  .suno-block h3 {{ color: #feca57; font-size: 20px; margin-bottom: 16px; }}
  .suno-block pre {{
    background: rgba(255,255,255,0.06); border-radius: 10px; padding: 18px;
    font-family: "SF Mono", "Fira Code", "Consolas", monospace;
    font-size: 13px; line-height: 1.8; overflow-x: auto; white-space: pre-wrap;
    color: #a8c8ff; border: 1px solid rgba(255,255,255,0.08);
  }}
  .suno-tags-top {{
    border-color: rgba(254,202,87,0.2) !important;
    margin-bottom: 16px;
  }}
  .lang-section {{
    position: relative; margin-bottom: 12px;
  }}
  .lang-section:last-child {{ margin-bottom: 0; }}
  .lang-pre {{
    margin: 0;
    background: rgba(255,255,255,0.04) !important;
    padding-bottom: 48px !important; /* 给按钮留空间 */
  }}
  #enContent {{ border-left: 3px solid #0984e3 !important; }}
  #cnContent {{ border-left: 3px solid #e17055 !important; }}
  .lang-copy-wrap {{
    position: absolute; bottom: 12px; right: 12px;
    display: flex; gap: 8px;
  }}
  .lang-copy-btn {{
    position: static !important;
    font-size: 12px !important; padding: 6px 14px !important;
    border-radius: 6px !important;
  }}
  .copy-btn {{
    position: absolute; top: 24px; right: 24px;
    background: #feca57; color: #1a1a2e; border: none;
    border-radius: 8px; padding: 8px 18px; font-size: 13px; font-weight: 700;
    cursor: pointer; transition: all 0.2s;
  }}
  .copy-btn:hover {{ background: #ff9f43; }}
  .copy-btn.copied {{ background: #00b894; color: #fff; }}

  /* 置信度条 */
  .confidence-bar {{
    display: flex; align-items: center; gap: 10px; margin: 6px 0;
  }}
  .bar-label {{ width: 50px; font-size: 12px; color: #8b8fa3; flex-shrink: 0; }}
  .bar-track {{ flex: 1; height: 8px; background: #e0e4f0; border-radius: 4px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.8s ease; }}
  .bar-value {{ font-size: 12px; font-weight: 700; width: 36px; flex-shrink: 0; }}

  /* 页脚 */
  .footer {{
    text-align: center; padding: 20px; font-size: 12px; color: #8b8fa3;
  }}

  /* 工具类 */
  .text-muted {{ color: #8b8fa3; font-size: 14px; }}
  .tag-list {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .tag {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; background: #e8f0fe; color: #0984e3; }}
  ul {{ padding-left: 20px; }}
  li {{ margin: 3px 0; font-size: 14px; }}

  @media (max-width: 600px) {{
    .header h1 {{ font-size: 22px; }}
    .header {{ padding: 24px 20px; }}
    .section {{ padding: 20px; }}
    .copy-btn {{ position: static; display: block; width: 100%; margin-bottom: 12px; }}
    .lang-copy-wrap {{ position: static !important; }}
    .lang-copy-btn {{ width: auto !important; display: inline-block !important; }}
    .overview {{ grid-template-columns: repeat(2, 1fr); }}
  }}
</style>
</head>
<body>
<div class="container">

  <!-- 头部 -->
  <div class="header">
    <h1>🎵 歌曲音乐全维度分析报告</h1>
    <div class="subtitle">{original_filename}</div>
    <div class="meta">
      <span class="meta-item">📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}</span>
      <span class="meta-item">⏱ {dur_str}</span>
      <span class="meta-item">📁 {a.basic.format_name or 'N/A'}</span>
      <span class="meta-item">🔊 {a.basic.sample_rate}Hz / {a.basic.bitrate_kbps}kbps / {a.basic.channels}ch</span>
    </div>
  </div>

  <!-- 曲风亮点 -->
  <div class="genre-hero">
    <div class="primary-genre">{a.genre.primary_genre_cn}</div>
    <div class="genre-en">{a.genre.primary_genre_en}</div>
    <div style="margin-top:10px">{_confidence_bar(a.genre.genre_confidence, "曲风置信度")}</div>
    <div class="genre-meta">
      <span class="genre-tag primary">{a.genre.regional_style}</span>
      <span class="genre-tag primary">{a.genre.genre_tags_cn[0] if a.genre.genre_tags_cn else ''}</span>
      {secondary_genre_rows}
    </div>
    <p style="margin-top:12px;font-size:13px;color:#636e72;">{a.genre.cultural_influence}</p>
  </div>

  <!-- 概览 -->
  <div class="overview">
    <div class="overview-card">
      <div class="label">BPM</div>
      <div class="value">{bpm}</div>
      <div class="sub">{a.tempo.tempo_class_cn}</div>
    </div>
    <div class="overview-card">
      <div class="label">调式</div>
      <div class="value">{a.key.detected_key} {'大' if a.key.detected_mode=='major' else '小'}调</div>
      <div class="sub">{int(a.key.confidence*100)}% 置信度</div>
    </div>
    <div class="overview-card">
      <div class="label">情绪</div>
      <div class="value">{a.mood.primary_emotion_cn}</div>
      <div class="sub">{a.mood.valence_estimate}</div>
    </div>
    <div class="overview-card">
      <div class="label">人声</div>
      <div class="value">{'纯器乐' if not a.vocals.has_vocals else ('男声' if '男' in a.vocals.vocal_gender else ('女声' if '女' in a.vocals.vocal_gender else '有'))}</div>
      <div class="sub">{'Rap' if a.vocals.is_rap else ''}</div>
    </div>
    <div class="overview-card">
      <div class="label">乐器</div>
      <div class="value">{len(a.instruments.likely_instruments)}类</div>
      <div class="sub">{'电子' if a.instruments.is_electronic else ('管弦' if a.instruments.is_orchestral else '')}</div>
    </div>
  </div>

  <!-- 一、基础信息 -->
  <div class="section">
    <div class="section-header"><div class="section-num c1">1</div><div class="section-title">基础信息</div></div>
    <div class="section-grid">
      <div class="section-item"><div class="key">格式</div><div class="val">{a.basic.format_name or 'N/A'}</div></div>
      <div class="section-item"><div class="key">时长</div><div class="val">{dur_str}</div></div>
      <div class="section-item"><div class="key">采样率</div><div class="val">{a.basic.sample_rate} Hz</div></div>
      <div class="section-item"><div class="key">比特率</div><div class="val">{a.basic.bitrate_kbps} kbps</div></div>
      <div class="section-item"><div class="key">声道</div><div class="val">{a.basic.channels}声道 ({'立体声' if a.basic.channels>=2 else '单声道'})</div></div>
      <div class="section-item"><div class="key">编码</div><div class="val">{a.basic.codec or 'N/A'}</div></div>
    </div>
  </div>

  <!-- 二、BPM / 速度 -->
  <div class="section">
    <div class="section-header"><div class="section-num c2">2</div><div class="section-title">BPM / 速度</div></div>
    <div class="section-grid">
      <div class="section-item"><div class="key">BPM</div><div class="val">{bpm}</div></div>
      <div class="section-item"><div class="key">速度分类</div><div class="val">{a.tempo.tempo_class_cn} ({a.tempo.tempo_class})</div></div>
      <div class="section-item"><div class="key">节拍强度</div><div class="val">{'较强' if a.tempo.beat_strength>0.3 else '中等' if a.tempo.beat_strength>0.1 else '较弱'}</div></div>
    </div>
    {_confidence_bar(a.tempo.bpm_confidence, "BPM 置信度")}
  </div>

  <!-- 三、调式分析 -->
  <div class="section">
    <div class="section-header"><div class="section-num c3">3</div><div class="section-title">调式分析</div></div>
    <div class="section-grid">
      <div class="section-item"><div class="key">主调式</div><div class="val">{a.key.detected_key} {'大调 (Major)' if a.key.detected_mode=='major' else '小调 (Minor)'}</div></div>
      <div class="section-item"><div class="key">色彩</div><div class="val">{'明亮、积极' if a.key.detected_mode=='major' else '忧伤、深沉、戏剧性'}</div></div>
    </div>
    {_confidence_bar(a.key.confidence, "调式置信度")}
    {f'<div class="tag-list" style="margin-top:12px"><span class="text-muted">备选调式: </span>{"".join(alt_key_rows)}</div>' if alt_key_rows else ''}
  </div>

  <!-- 四、节奏特征 -->
  <div class="section">
    <div class="section-header"><div class="section-num c4">4</div><div class="section-title">节奏特征</div></div>
    <div class="section-grid">
      <div class="section-item"><div class="key">拍号</div><div class="val">{a.rhythm.time_signature} 拍</div></div>
      <div class="section-item"><div class="key">节奏复杂度</div><div class="val">{a.rhythm.rhythm_complexity_cn or '未检测'}</div></div>
      <div class="section-item"><div class="key">切分音</div><div class="val">{a.rhythm.syncopation_level_cn or '未检测'}</div></div>
      <div class="section-item"><div class="key">摇摆感</div><div class="val">{f'{a.rhythm.swing_ratio:.2f}' if a.rhythm.swing_ratio>0 else '—'}</div></div>
    </div>
  </div>

  <!-- 五、频谱音色 -->
  <div class="section">
    <div class="section-header"><div class="section-num c5">5</div><div class="section-title">频谱音色</div></div>
    <div class="section-grid">
      <div class="section-item"><div class="key">频谱质心</div><div class="val">{a.spectral.centroid_mean} Hz</div></div>
      <div class="section-item"><div class="key">音色描述</div><div class="val">{a.spectral.timbre_description}</div></div>
      <div class="section-item"><div class="key">频谱带宽</div><div class="val">{a.spectral.bandwidth_mean} Hz</div></div>
      <div class="section-item"><div class="key">滚降频率</div><div class="val">{a.spectral.rolloff_mean} Hz</div></div>
    </div>
  </div>

  <!-- 六、能量动态 -->
  <div class="section">
    <div class="section-header"><div class="section-num c6">6</div><div class="section-title">能量动态</div></div>
    <div class="section-grid">
      <div class="section-item"><div class="key">平均能量</div><div class="val">{a.energy.rms_mean:.4f}</div></div>
      <div class="section-item"><div class="key">峰值能量</div><div class="val">{a.energy.rms_max:.4f}</div></div>
      <div class="section-item"><div class="key">动态范围</div><div class="val">{a.energy.dynamic_range_db} dB</div></div>
      <div class="section-item"><div class="key">压缩程度</div><div class="val">{a.energy.compression_feel_cn}</div></div>
    </div>
  </div>

  <!-- 七、乐器推测 -->
  <div class="section">
    <div class="section-header"><div class="section-num c7">7</div><div class="section-title">乐器推测</div></div>
    <div class="section-grid">
      <div class="section-item"><div class="key">和声/打击比</div><div class="val">{a.instruments.harmonic_energy_ratio*100:.0f}% / {a.instruments.percussive_energy_ratio*100:.0f}%</div></div>
      <div class="section-item"><div class="key">电子元素</div><div class="val">{'是' if a.instruments.is_electronic else '否'}</div></div>
      <div class="section-item"><div class="key">管弦编制</div><div class="val">{'是' if a.instruments.is_orchestral else '否'}</div></div>
    </div>
    <div class="tag-list" style="margin-top:12px">{'  '.join(f'<span class="tag">{inst.split(" (")[0]}</span>' for inst in a.instruments.likely_instruments)}</div>
  </div>

  <!-- 八、人声特征 -->
  <div class="section">
    <div class="section-header"><div class="section-num c8">8</div><div class="section-title">人声特征</div></div>
    <div class="section-grid">
      <div class="section-item"><div class="key">是否有人声</div><div class="val">{'有' if a.vocals.has_vocals else '无（纯器乐）'}</div></div>
      <div class="section-item"><div class="key">声音倾向</div><div class="val">{a.vocals.vocal_gender or '—'}</div></div>
      <div class="section-item"><div class="key">音域</div><div class="val">{a.vocals.vocal_range_approx or '—'}</div></div>
      <div class="section-item"><div class="key">是否Rap</div><div class="val">{'是' if a.vocals.is_rap else '否'}</div></div>
    </div>
    {vocal_tags_html}
  </div>

  <!-- 九、情绪氛围 -->
  <div class="section">
    <div class="section-header"><div class="section-num c9">9</div><div class="section-title">情绪氛围</div></div>
    <div class="section-grid">
      <div class="section-item"><div class="key">主要情绪</div><div class="val">{a.mood.primary_emotion_cn}</div></div>
      <div class="section-item"><div class="key">愉悦度(效价)</div><div class="val">{a.mood.valence_estimate}</div></div>
      <div class="section-item"><div class="key">唤醒度</div><div class="val">{a.mood.arousal_estimate}</div></div>
    </div>
    {mood_tags_html}
  </div>

  <!-- 十、和声走向 -->
  <div class="section">
    <div class="section-header"><div class="section-num c10">10</div><div class="section-title">和声走向 / 和弦进行</div></div>
    {harmony_detail}
    {chord_tags_html}
  </div>

  <!-- 十一、制作特征 -->
  <div class="section">
    <div class="section-header"><div class="section-num c11">11</div><div class="section-title">制作特征</div></div>
    <div class="section-grid">
      <div class="section-item"><div class="key">混响程度</div><div class="val">{a.production.reverb_level_cn}</div></div>
      <div class="section-item"><div class="key">立体声宽度</div><div class="val">{a.production.stereo_width_cn}</div></div>
      <div class="section-item"><div class="key">响度</div><div class="val">{'约 '+str(a.production.loudness_lufs)+' LUFS' if a.production.loudness_lufs > -900 else '无法评估'}</div></div>
      <div class="section-item"><div class="key">制作年代感</div><div class="val">{a.production.production_era_cn}</div></div>
      <div class="section-item"><div class="key">混音质量</div><div class="val">{a.production.mix_quality}</div></div>
    </div>
  </div>

  <!-- 十二、曲风归类 -->
  <div class="section" style="border:2px solid #d63031;">
    <div class="section-header"><div class="section-num c12">12</div><div class="section-title">曲风归类</div></div>
    <div class="section-grid">
      <div class="section-item"><div class="key">主曲风</div><div class="val">{a.genre.primary_genre_cn}</div></div>
      <div class="section-item"><div class="key">英文名称</div><div class="val">{a.genre.primary_genre_en}</div></div>
      <div class="section-item"><div class="key">地域风格</div><div class="val">{a.genre.regional_style}</div></div>
      <div class="section-item"><div class="key">文化影响</div><div class="val" style="font-size:12px;line-height:1.4">{a.genre.cultural_influence}</div></div>
    </div>
    {_confidence_bar(a.genre.genre_confidence, "曲风置信度")}
    <div class="tag-list" style="margin-top:12px">
      {' '.join(f'<span class="tag" style="background:#fce4e4;color:#d63031">{t}</span>' for t in a.genre.genre_tags_cn)}
    </div>
    {secondary_genre_html}
  </div>

  <!-- Suno 提示词 -->
  <div class="suno-block">
    <button class="copy-btn" onclick="copySuno()" id="copyBtn">📋 一键复制（全量）</button>
    <h3>★ 可直接复制到 Suno AI 的综合提示词</h3>

    <!-- 标签头部 -->
    <pre class="suno-tags-top">{suno_tags_top}</pre>

    <!-- 英文提示词 + 英文复制按钮 -->
    <div class="lang-section">
      <pre class="lang-pre" id="enContent">{en_block}</pre>
      <div class="lang-copy-wrap">
        <button class="copy-btn lang-copy-btn" onclick="copyEn()">📋 英文复制</button>
      </div>
    </div>

    <!-- 中文提示词 + 中文复制按钮 -->
    <div class="lang-section">
      <pre class="lang-pre" id="cnContent">{cn_block}</pre>
      <div class="lang-copy-wrap">
        <button class="copy-btn lang-copy-btn" onclick="copyCn()">📋 中文复制</button>
      </div>
    </div>
  </div>

  <div class="footer">
    <p>📋 使用说明：点击「一键复制」按钮 → 打开 Suno AI → 粘贴到 Style Prompt / Lyrics 框 → 生成</p>
    <p style="margin-top:4px">Generated by Song Analyzer Skill v1.3.1 | 12维度深度分析</p>
  </div>

</div>

<script>
// --- 稳健复制工具函数 ---
function stripLabel(text, label) {{
  // 去掉开头的 [English] / [中文] 标签及紧随的换行
  const re = new RegExp('^\\\\[' + label + '\\\\]\\\\s*\\\\r?\\\\n?');
  return text.replace(re, '');
}}

function safeCopy(content, btn, originalText) {{
  try {{
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(content).then(function() {{
        if (btn) flashBtn(btn, originalText);
      }}).catch(function() {{
        fallbackCopy(content);
        if (btn) flashBtn(btn, originalText);
      }});
    }} else {{
      fallbackCopy(content);
      if (btn) flashBtn(btn, originalText);
    }}
  }} catch(e) {{
    fallbackCopy(content);
    if (btn) flashBtn(btn, originalText);
  }}
}}

function flashBtn(btn, originalText) {{
  btn.textContent = '✅ 已复制';
  btn.classList.add('copied');
  setTimeout(function() {{ btn.textContent = originalText; btn.classList.remove('copied'); }}, 2000);
}}

function fallbackCopy(content) {{
  var ta = document.createElement('textarea');
  ta.value = content; ta.style.position = 'fixed'; ta.style.left = '-9999px'; ta.style.top = '0';
  document.body.appendChild(ta); ta.focus(); ta.select();
  try {{ document.execCommand('copy'); }} catch(e) {{}}
  document.body.removeChild(ta);
}}

// --- 各复制入口 ---
function copySuno() {{
  var tagsTop = document.querySelector('.suno-tags-top');
  var enBlock = document.getElementById('enContent');
  var cnBlock = document.getElementById('cnContent');
  var tagsText = tagsTop ? tagsTop.textContent : '';
  var enText = enBlock ? stripLabel(enBlock.textContent, 'English') : '';
  var cnText = cnBlock ? stripLabel(cnBlock.textContent, '中文') : '';
  var parts = [tagsText, enText, cnText].filter(function(t) {{ return t.trim(); }});
  var btn = document.getElementById('copyBtn');
  safeCopy(parts.join('\\n\\n'), btn, '📋 一键复制（全量）');
}}

function copyEn() {{
  var el = document.getElementById('enContent');
  if (!el) return;
  var text = stripLabel(el.textContent, 'English');
  var btn = document.querySelector('#enContent + .lang-copy-wrap .lang-copy-btn');
  safeCopy(text, btn, '📋 英文复制');
}}

function copyCn() {{
  var el = document.getElementById('cnContent');
  if (!el) return;
  var text = stripLabel(el.textContent, '中文');
  var btn = document.querySelector('#cnContent + .lang-copy-wrap .lang-copy-btn');
  safeCopy(text, btn, '📋 中文复制');
}}
</script>
</body>
</html>'''

    return html


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="歌曲/音乐全面分析工具 — 输出 HTML 可视化报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="音频文件路径 (MP3/MP4/WAV/FLAC/M4A/OGG)")
    parser.add_argument("-o", "--output-dir", default=".",
                        help="输出目录 (默认: 当前目录)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        analyzer = AudioAnalyzer(args.input)
        analysis = analyzer.run_full_analysis()
    except Exception as e:
        print(f"\n[FATAL] 分析失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 生成输出文件名（仅 HTML）
    input_stem = Path(args.input).stem
    original_name = Path(args.input).name
    html_path = output_dir / f"歌曲分析报告_{input_stem}.html"

    # 生成 Suno 提示词（嵌入 HTML，不单独写文件）
    suno = generate_suno_prompt(analysis)

    # 生成 HTML 静态报告（唯一产物）
    html_report = generate_html_report(analysis, suno, original_name)
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_report)
    except OSError as e:
        print(f"[WARN] 无法写入 HTML 报告: {e}", file=sys.stderr)
        sys.exit(1)

    # 终端输出摘要
    print(f"\n{'='*60}")
    print(f"✅ 分析完成：{Path(args.input).name}")
    print(f"{'='*60}")
    print(f"  🎭 曲风:   {analysis.genre.primary_genre_cn} ({analysis.genre.primary_genre_en})")
    print(f"  🏷  地域:   {analysis.genre.regional_style}")
    print(f"  BPM:      {analysis.tempo.bpm} ({analysis.tempo.tempo_class_cn})")
    print(f"  调式:     {analysis.key.detected_key} {analysis.key.detected_mode}")
    print(f"  情绪:     {analysis.mood.primary_emotion_cn}")
    print(f"  乐器:     {', '.join(analysis.instruments.likely_instruments) if analysis.instruments.likely_instruments else '未检测到明显乐器'}")
    print(f"  人声:     {analysis.vocals.vocal_gender or ('纯器乐' if not analysis.vocals.has_vocals else '有')}")
    print(f"  节奏:     {analysis.rhythm.rhythm_complexity_cn}")
    print(f"  音色:     {analysis.spectral.timbre_description}")
    print(f"  动态:     {analysis.energy.compression_feel_cn}")
    print(f"  混响:     {analysis.production.reverb_level_cn}")
    print(f"  立体声:   {analysis.production.stereo_width_cn}")
    if analysis.genre.secondary_genres:
        secondaries = ', '.join(f'{sg["cn"]}({int(sg["confidence"]*100)}%)' for sg in analysis.genre.secondary_genres[:3])
        print(f"  次要曲风: {secondaries}")
    print(f"{'='*60}")
    print(f"\n📊 HTML 报告: {html_path}")
    print(f"\n--- Suno Prompt (EN) ---")
    print(suno["suno_prompt_en"])
    print(f"\n--- Suno 提示词 (CN) ---")
    print(suno["suno_prompt_cn"])
    print(f"\n--- Suno V4 Style Tags ---")
    print(suno["sunov4_style_prompt"])


if __name__ == "__main__":
    import librosa  # trigger import to verify availability
    main()
