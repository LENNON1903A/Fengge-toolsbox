---
name: song-analyzer
version: 1.3.1
description: >
  歌曲音乐全维度分析工具。对 MP3/MP4/WAV/FLAC 等音频文件，或 YouTube/B站/网易云/QQ音乐/头条/抖音/SoundCloud 等网络链接，
  提取 BPM、调式、乐器配器、人声特征、情绪氛围、曲式结构、节奏复杂度、频谱音色、能量动态、制作特征、
  和声走向和弦进行、曲风归类（校园风、阿拉伯风、日本风、中国风等26类）等全部音乐元素。
  唯一产物为一个静态 HTML 可视化报告，内含完整的12维度分析数据与双语 Suno AI 风格提示词（标签式 + 自然语言式 + 和弦标签），
  可直接在浏览器中查看，英文/中文提示词各自带独立复制按钮（自动去标签前缀）。
  不产生任何中间文件（JSON/TXT/单独 Suno 提示词文件）——只有这个 HTML。
  触发词：歌曲分析、音乐分析、分析这首歌、Suno风格、模仿风格、song analysis、analyze song、
  歌曲要素、音乐元素、音频分析、BPM、调式分析、乐器分析、曲风归类。
---

# 歌曲分析 Skill — Song Analyzer v1.3.1

## 概述

对任意歌曲/音乐进行 13 维度深度分析，**报告展示 12 维**（曲式结构仅用于 Suno 提示词，不在报告中展示），**唯一产物为一个 HTML 文件**：

- **静态 `.html` 可视化报告** — 醒目卡片式布局，12维度数据一目了然，内嵌双语 Suno 提示词（V4 标签式 + `[Chords]` + `[Structure]` + 英中自然语言），英文/中文提示词各自带独立复制按钮

> 不再单独输出 JSON、TXT 或 Suno 提示词文件。所有内容全部整合在一个 HTML 中，干净利落。

## 输入形式

接受三种输入：

| 输入类型 | 示例 | 处理方式 |
|---------|------|---------|
| **网络链接** | YouTube/B站/网易云/QQ音乐/头条/抖音/SoundCloud 分享链接 | `downloader.py` → yt-dlp 下载音频 |
| **本地 MP4** | `D:/Desktop/song.mp4` | `downloader.py` → ffmpeg 提取音频轨道 |
| **本地音频** | `.mp3` / `.wav` / `.flac` / `.m4a` / `.ogg` | 直接使用（非 WAV 自动转换） |

对于**微信视频号**等 yt-dlp 不支持的封闭平台：提示用户手动下载后提供本地文件路径。

## 前置条件

所有依赖由 WorkBuddy 管理，无需用户手动安装：

- **Python 环境**: `C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（隔离 venv）
- **Python 包**: librosa, numpy, scipy, soundfile, pydub
- **系统工具**: ffmpeg/ffprobe, yt-dlp

运行前验证：

```bash
# 验证 ffmpeg
ffmpeg -version 2>&1 | head -1

# 验证 yt-dlp
yt-dlp --version

# 验证 Python 包
"C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -c "import librosa; print('librosa', librosa.__version__)"
```

## 工作流程

### Phase 1: 输入预处理

调用 `scripts/downloader.py` 处理输入：

```bash
"C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" scripts/downloader.py "<用户输入>" -o "<临时输出目录>"
```

输出 JSON 格式：
```json
{
  "audio_file": "D:/temp/xxx.wav",
  "success": true,
  "method": "yt-dlp",
  "platform": "youtube"
}
```

**如果 `success: false`**：向用户报告 `error` 字段内容。

**如果平台是 `wechat_video`**：告知用户微信视频号不支持自动下载，请提供本地文件。

### Phase 2: 核心分析

使用 `scripts/audio_analyzer.py` 对预处理后的音频执行全维度分析：

```bash
"C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" scripts/audio_analyzer.py "<audio_file>" -o "D:/Desktop/"
```

**容错设计**（v1.2.0）：13 个分析步骤各自独立 try/except，单步骤失败不影响其他步骤，每个失败步骤打印 ✗ 并继续。

**即时呈现**：每完成一个分析维度即时打印 ✓，13 维分析完成后输出含 12 维展示数据的 HTML 文件。

#### 性能优化（v1.2.0）

核心计算缓存复用，避免重复的 STFT/MFCC/RMS 计算：

| 缓存属性 | 计算方法 | 使用方 |
|---------|---------|--------|
| `_get_stft()` | `np.abs(librosa.stft(y))` | `analyze_instruments`, `analyze_vocals`, `analyze_production` |
| `_get_mfcc()` | `librosa.feature.mfcc(y, n_mfcc=13)` | `analyze_vocals`, `analyze_structure` |
| `_get_rms()` | `librosa.feature.rms(y)` | `analyze_energy`, `analyze_production` |

全部惰性初始化，首次调用时计算并缓存，后续复用。

#### 健壮性（v1.2.0）

- **ffprobe 容错**：`duration`/`bit_rate` 为 `"N/A"` 字符串时 `try/except` 回退到 0
- **自相关数组边界**：`ac[10:]` 切片前检查 `len(ac) > 10`（防止空数组 `np.max` 崩溃）
- **corrcoef NaN**：对常数输入的色度图进行 `np.isnan()` 检查后替换为 0.0
- **文件写入异常**：HTML 写入失败时会 `sys.exit(1)` 报错终止，确保用户不会误以为分析成功
- **Unicode 路径**：`_load_audio` 直接加载失败后自动复制到 ASCII 临时文件再加载

**对于纯音乐（无歌词）**：分析会额外关注以下维度，在 Suno 提示词中强调：
- **旋律线条** — 频谱质心变化曲线反映旋律走向
- **和声进行** — 色度图分析暗示和弦模式
- **器乐编制** — HPSS 分离 + 频段能量分析

输出单个文件到桌面：
- `歌曲分析报告_<歌名>.html` — 静态HTML可视化报告（醒目卡片式，内嵌全部分析数据与Suno提示词，含一键复制按钮）

### Phase 3: 结果呈现

**仅呈现一个 HTML 文件**（自动保存到桌面）：

- 在浏览器中打开查看12维度数据
- 英文/中文区块各自独立复制按钮（复制内容自动去除 `[English]`/`[中文]` 前缀）
- 顶部「📋 一键复制（全量）」— V4标签+英文+中文三合一
- HTML 文件是唯一的持久化产物，分析完成后**不保留任何中间文件**（JSON/TXT/Suno JSON）

在文本回复中以代码块展示关键参数摘要：

```
BPM: 128 (Allegro)  |  调式: C# minor
情绪: 激烈紧张       |  乐器: 鼓组, 贝斯, 吉他, 合成器
人声: 男声倾向      |  音色: 明亮/清脆
```

## 分析维度说明

| # | 维度 | 关键指标 | 对 Suno 的作用 |
|---|------|---------|---------------|
| 1 | 基础信息 | 格式/时长/采样率/比特率/声道 | 音质参考 |
| 2 | BPM/速度 | 精确 BPM + 速度分类(Largo~Presto) | 节奏速度设定 |
| 3 | 调式 | 调性(C/D/E…) + 大/小调 + 置信度 | Key 参数，决定整体色彩 |
| 4 | 节奏 | 复杂度/切分音/摇摆感 | 节奏风格描述 |
| 5 | 频谱 | 质心(明亮度)/带宽/对比度/过零率 | 音色风格标签 |
| 6 | 能量 | RMS 均值/标准差/动态范围/峰值因子 | 动态/压缩风格 |
| 7 | 乐器 | HPSS 分离 → 鼓/Bass/吉他/合成器/弦乐/铜管 | 配器描述 |
| 8 | 人声 | 有/无、性别、音域、气声/力量/饱满、Rap | 人声风格标签 |
| — | ~~曲式~~ | ~~段落数、Intro/Verse/Chorus/Bridge/Outro~~ | 仅用于 Suno `[Structure:]` 标签，不在报告中展示 |
| 9 | 情绪 | 效价 + 唤醒度 → 情绪分类 + 次要情绪列表 | 氛围/情绪关键词 |
| 10 | 和声走向 | 色度图 → 和弦模板匹配 → 常见进行式(13种) | `[Chords: I-V-vi-IV]` |
| 11 | 制作 | 混响/立体声宽度/响度(LUFS)/制作年代感 | 混音参考 |
| 12 | 曲风归类 | 26类规则引擎 → 校园风/阿拉伯风/日本风/中国风等 | 流派风格标签 `[Genre:]` |

## Suno 提示词格式

### V4 标签式（直接用于 Suno Style Prompt）
```
[Genre: jazz][Key: E major][BPM: 172.3][Instruments: Bass, Guitar, Keys/Synth, Strings, Brass, Orchestral][Vocals: male vocals][Mood: Neutral][Production: high_dynamic, high][Structure: verse → chorus]
```

### 自然语言式（按 Suno 推荐顺序排列）
曲风 → 调式 → 速度 → 配器 → 人声（含风格细节）→ 情绪 → 音色 → 和声 → 节奏 → 曲式结构（前奏/主歌/副歌/桥段/间奏/尾奏）→ 制作 → 地域

### HTML 复制按钮
- 英文提示词右下方「📋 英文复制」— 仅复制英文自然语言（自动去 `[English]` 前缀）
- 中文提示词右下方「📋 中文复制」— 仅复制中文自然语言（自动去 `[中文]` 前缀）
- 顶部「📋 一键复制（全量）」— 复制 V4 标签 + 英文 + 中文（三合一）
- 按钮点击后变为「✅ 已复制」绿色，2 秒后恢复

## 平台下载策略链

```
URL 输入
  ├─ YouTube/B站/SoundCloud → yt-dlp 直接下载
  ├─ 网易云/QQ音乐        → yt-dlp（可能需要 cookie）
  ├─ 今日头条             → yt-dlp → 失败→移动端 API 回退
  ├─ 抖音                 → yt-dlp（douyin extractor, 移动端 UA）
  ├─ Bing视频             → 不支持（聚合页，需用户提取原始视频链接）
  └─ 微信视频号           → 告知用户手动下载
```

yt-dlp 命令模板（在 `downloader.py` 中自动执行）：
```bash
yt-dlp --no-check-certificates -x --audio-format wav --audio-quality 0 -o "%(title)s.%(ext)s" <URL>
```

头条/抖音须使用 Android 移动端 User-Agent 以避免反爬。

> **B站注意**：B站近期加强反爬（HTTP 412），yt-dlp 可能下载失败。此时需更新 yt-dlp 或改用其他平台源。

> **Bing视频注意**：Bing视频搜索是聚合页面，不包含可直接下载的视频源。需在Bing页面点击跳转到原始视频来源后提供原始链接。

## 输出文件结构

```
D:/Desktop/
└── 歌曲分析报告_<歌名>.html      # 唯一产物：静态 HTML 可视化报告（含12维度展示 + Suno提示词）
```

> 不再产生 `_analysis.json`、`_suno_prompt.json`、`.txt` 等中间文件。

## 使用示例

**示例 1：分析本地 MP3 文件**
```
用户：分析这首歌 D:/Desktop/如果草原(男声版).mp3
→ 分析器直接加载音频
→ 13 维度顺序分析输出 ✓
→ 1 个 HTML 文件保存到桌面 → present_files 展示
```

**示例 2：分析 YouTube 链接**
```
用户：分析这首歌 https://www.youtube.com/watch?v=dQw4w9WgXcQ
→ 下载器用 yt-dlp 下载音频
→ 分析器生成 HTML 报告到桌面 → present_files 展示
```

**示例 3：分析纯音乐**
```
用户：分析这个纯音乐 D:/Desktop/orchestral.flac
→ 检测到无歌词
→ 分析重点转向旋律线条、和声进行、器乐编制
→ HTML 报告中 Suno 提示词标注 [instrumental]
→ present_files 展示
```

## 已知限制

- **立体声分析**：`analyze_production` 为获取立体声宽度重新从磁盘加载（限制 120 秒），无法复用内存中的单声道 `_y`
- **频谱特征**：`spectral_centroid`/`bandwidth`/`rolloff` 内部独立计算 STFT，无法与 `_get_stft()` 共用（接口不兼容 `S=` 参数）
- **HPSS 失败**：`_y_harm`/`_y_perc` 回退为 None，调式和声分析自动使用 `_y` 替代
- **路径限制**：ffmpeg 搜索路径硬编码 Windows 常见路径（`C:\ffmpeg\bin` 等），非 Windows 平台需手动配置
- **分析时间**：3-5 分钟歌曲约 20-50 秒（CPU），受 librosa 色度图/STFT 计算瓶颈限制
- **B站反爬**：B站近期返回 HTTP 412，yt-dlp 可能失败，需更新 yt-dlp 或换源
- **Bing视频**：聚合搜索页无法直接下载，需提取原始视频链接
- **曲风归类精度**：规则引擎基于 BPM/调式/乐器等特征推理，存在误判可能（如将日本电影配乐归类为爵士）

## 注意事项

- 分析一首 4 分钟歌曲约需 20-50 秒，取决于 CPU 性能
- yt-dlp 下载受网络影响，超时设为 120 秒
- 临时下载文件存放在系统临时目录，分析完成后可清理
- Suno 对英文 prompt 响应更好，优先使用 `suno_prompt_en`
- 纯音乐分析时额外关注色度图和频谱质心变化曲线
- 如果 ffmpeg 或 yt-dlp 未安装，先执行环境验证并安装

## 版本历史

### v1.3.1 (2026-06-21)
- **移除曲式结构板块**：第9号「曲式结构」从 HTML 报告和文本报告中删除（曲式分析逻辑保留，Suno 提示词 structure 字段正常输出）
- **序号修正**：情绪氛围 10→9、和声走向 11→10、制作特征 12→11、曲风归类 13→12
- **标题修改**：「歌曲音乐全维度分析报告」→「音乐分析报告」
- **文档同步**：SKILL.md / audio_analyzer.py docstring / HTML footer 版本号同步更新为 12 维展示
- **Bing视频处理**：downloader.py 新增 `bing_video` 平台识别，返回明确错误提示（聚合页不可直接下载）
- **注释修正**：次要曲风标签注释从"第十三维度"→"第十二维度"

### v1.3.0 (2026-06-20)
- **精简产物**：不再输出 JSON/TXT/Suno JSON 中间文件，唯一产物为 HTML 可视化报告
- **HTML 写入严格化**：写入失败直接 `sys.exit(1)`，不再静默跳过
- **SKILL.md 重写**：移除所有多文件输出相关描述

### v1.2.0 (2026-06-20)
- **健壮性**：13 步骤独立 try/except、ffprobe N/A 容错、corrcoef NaN 防护、文件写入异常隔离
- **性能**：STFT/MFCC/RMS 惰性缓存复用，减少重复计算
- **修复**：`analyze_energy` 缺失 `import librosa`、`ac[10:]` 空数组边界、`shutil` 导入规范
- **代码清理**：移除未使用的 `math`/`Counter`/`Optional` 导入、`__import__('datetime')` 改为正常 import
- **复制按钮**：`textContent` 替代 `innerText`、`safeCopy()` 统一封装、所有分支均闪动按钮

### v1.1.0 (2026-06-20)
- 新增第13维「曲风归类」(26类规则引擎)
- 新增 HTML 静态可视化报告
- 英文/中文独立复制按钮、Suno 提示词按风格逻辑排序
- 曲式结构含前奏/间奏/尾奏全流程

### v1.0.0 (2026-06-20)
- 初始版本：11维分析 + 双语 Suno 提示词 + 纯文本报告
