# Bilibili Spider Toolkit

A pure Python toolkit for downloading videos and bangumi (anime/series) from Bilibili. Supports regular videos (BV ID), bangumi (SS/EP ID), and search-based batch downloads.

> 🇨🇳 **中文版**: [README.md](README.md)

---

## Table of Contents

- [Features](#features)
- [Files](#files)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Cookie Setup](#cookie-setup)
- [FFmpeg Setup](#ffmpeg-setup)
- [Usage](#usage)
- [Output Structure](#output-structure)
- [Disclaimer / What You Cannot Do](#disclaimer--what-you-cannot-do)
- [FAQ](#faq)
- [Technical Details](#technical-details)
- [License](#license)

---

## Features

### 🎛️ Master Controller (main.py)

- ✅ **Unified entry point**: One program calls all three spider scripts
- ✅ **Mixed batch tasks**: Custom task queue with single video / search / bangumi downloads
- ✅ **Unified Cookie management**: Set once, used by all three scripts
- ✅ **Summary report**: Auto-generated summary report after batch tasks
- ✅ **Task list management**: Add, view, and clear tasks

### 🔍 Search & Batch Download Spider (bsps.py / bsps - 副本.py)

- ✅ Keyword-based video search, auto-extracts BV IDs
- ✅ **Multi-process concurrent download**, dynamically allocated (1~8 processes)
- ✅ **Multi-threaded chunked download**, up to 8 threads per file
- ✅ Highest quality by default, auto-picks best codec
- ✅ Filename includes publish date: `[YYYY-MM-DD] Title.mp4`
- ✅ Auto FFmpeg merge, output to `output/` directory
- ✅ Two-stage pipeline: Download phase → Merge phase, more efficient
- ✅ Per-video logs with process ID prefix
- ✅ Detailed summary at the end (success/fail list, total time)
- ✅ Request interval delay, simulates real user behavior
- ✅ Dual log output: console + file
- ✅ Both minimal and fully-commented versions available

### 📺 Bangumi Spider (bsp.py / bilibili_pgc_spider - 副本.py)

- ✅ Supports both SS (season) and EP (episode) URLs
- ✅ Auto-parses both DASH and DURL video formats
- ✅ Multiple quality options, highest quality by default
- ✅ Auto-deduplication for same quality (picks highest bitrate among H.264/H.265/AV1)
- ✅ Video and audio downloaded separately, stored in different folders
- ✅ Optional one-click FFmpeg merge to MP4 (lossless, seconds to complete)
- ✅ Supports both `ffmpeg-python` library and command-line invocation
- ✅ Supports manual FFmpeg path specification (env var or code config)
- ✅ Automatic cookie validity check
- ✅ Real-time download progress display
- ✅ Dual log output: console + file
- ✅ Automatic filename sanitization

### 🎬 Regular Video Spider (bspv.py)

- ✅ Supports BV ID video downloads
- ✅ DASH format parsing, separate video/audio
- ✅ Manual quality ID selection
- ✅ Optional video/audio saving
- ✅ Automatic cookie validity check
- ✅ Dual log output: console + file

---

## Files

### Core Scripts

| Filename | Type | Description |
|----------|------|-------------|
| `main.py` | Master controller | Unified entry point with mixed batch task scheduling |
| `bsps.py` | Search & batch download (minimal) | Keyword search + multi-process batch download |
| `bsps - 副本.py` | Search & batch download (commented) | Detailed comments, for learning code logic |
| `bsp.py` | Bangumi spider (minimal) | No extra comments, compact code, for daily use |
| `bilibili_pgc_spider - 副本.py` | Bangumi spider (commented) | Detailed comments on every function, for learning |
| `bspv.py` | Regular video spider | BV ID based video download tool |

### Config & Logs

| Filename | Type | Description |
|----------|------|-------------|
| `cookie.txt` | Config (create yourself) | Store your Bilibili login cookie, one line |
| `log/` | Log directory (auto-generated) | Independent log per run, named: `timestamp_scriptname.log` |
| `output/` | Output directory (auto-generated) | All download results in one place |

### Other Directories

| Directory | Description |
|-----------|-------------|
| `test/` | Beta scripts | Latest feature test versions, may include experimental features |

> 💡 The paired scripts have **identical functionality**, differing only in the amount of comments. Use whichever fits your needs.

---

## Requirements

### Required

- **Python 3.7+**
- **requests library**: `pip install requests`

### Optional

| Dependency | Purpose | Installation |
|------------|---------|--------------|
| `ffmpeg-python` | Python bindings for FFmpeg, used for merging | `pip install ffmpeg-python` |
| `FFmpeg` binary | Actual audio/video merge execution | See [FFmpeg Setup](#ffmpeg-setup) |
| `Bilibili account cookie` | Download high-quality / member-only resources | See [Cookie Setup](#cookie-setup) |

> ⚠️ Note: `ffmpeg-python` is just Python bindings and **does NOT include the FFmpeg binary**. You need to install FFmpeg separately.

---

## Quick Start

### 1. Install Dependencies

```bash
pip install requests
# Optional: if you want to use the ffmpeg-python library for merging
pip install ffmpeg-python
```

### 2. Configure Cookie (optional but recommended)

Create `cookie.txt` in the same directory as the script, paste your Bilibili cookie:

```
SESSDATA=your_SESSDATA; bili_jct=your_bili_jct; DedeUserID=your_UID
```

> Without a cookie, only low-quality anonymous resources can be downloaded.

### 3. Run the Script

**Master Controller (recommended):**
```bash
python main.py
# Choose: Single video / Search batch / Bangumi / Mixed batch tasks
```

**Search & batch download:**
```bash
python bsps.py
# Enter search keyword
# Enter number of videos to download (default 5)
# Auto search → multi-process download → auto merge
```

**Download bangumi:**
```bash
python bsp.py
# Enter the bangumi URL, e.g.: https://www.bilibili.com/bangumi/play/ss33415
```

**Download regular video:**
```bash
python bspv.py
# Enter the BV ID, e.g.: BV1GJ411x7h7
```

---

## Cookie Setup

### Why do I need a cookie?

Many Bilibili videos (especially 720P+, bangumi episodes, member-only content) require a login to get the playback URL.

### How to Get Your Cookie

1. Open [bilibili.com](https://www.bilibili.com) in your browser and log in
2. Press F12 to open Developer Tools
3. Switch to the **Network** tab
4. Refresh the page, click on any request
5. Find the `Cookie:` line in the request headers, copy the entire line
6. Paste it into the `cookie.txt` file

### Cookie Security Warning

- ⚠️ **Never upload your cookie to a public repository**
- ⚠️ **Never share your cookie with strangers**
- ⚠️ A cookie is equivalent to your account password — leaking it may lead to account theft
- It is recommended to add `cookie.txt` to `.gitignore`

---

## FFmpeg Setup

### Why do I need FFmpeg?

Bilibili DASH format stores video and audio separately. After downloading, they need to be merged into a single playable MP4 file.

### Installing FFmpeg

**Windows (recommended):**
- Official download: https://ffmpeg.org/download.html
- Or use winget: `winget install Gyan.FFmpeg`
- Or use scoop: `scoop install ffmpeg`

**Verify installation (make sure `ffmpeg` is in your system PATH):**
```bash
ffmpeg -version
```

### FFmpeg Path Configuration

The script looks for FFmpeg in the following priority order:

1. **Environment variable `FFMPEG_PATH`**: manually specify the full path to ffmpeg.exe
2. **System PATH auto-detection**: `shutil.which('ffmpeg')`
3. **None found**: skip merging, keep original files

**Manual path example (Windows):**
```python
# Modify at the top of the script:
FFMPEG_PATH = r'C:\ffmpeg\bin\ffmpeg.exe'
```

Or set an environment variable:
```bash
set FFMPEG_PATH=C:\ffmpeg\bin\ffmpeg.exe
python bsp.py
```

### Merge Methods

The script automatically chooses the available merge method:
- Priority: `ffmpeg-python` library (if installed and no manual path specified)
- Fallback: `subprocess` command-line invocation

---

## Usage

### Master Controller (main.py)

**Unified entry point with three download modes and mixed batch tasks.**

**Main menu:**
```
  1. Single video download (BV ID)
  2. Search & batch download
  3. Bangumi download (PGC)
  4. Mixed batch tasks
  5. Set Cookie
  0. Exit
```

**Mixed batch task commands:**

| Command | Description | Example |
|---------|-------------|---------|
| `bv <BV_ID> [quality_ID]` | Add single video task | `bv BV1xx411c7mD 116` |
| `search <keyword> [count]` | Add search download task | `search Python tutorial 10` |
| `pgc <URL>` | Add bangumi download task | `pgc https://www.bilibili.com/bangumi/play/epxxx` |
| `list` | View current task list | |
| `clear` | Clear task list | |
| `done` | Start executing all tasks | |

### Search & Batch Download (bsps.py)

**Keyword-based video search with multi-process batch download + multi-thread chunked acceleration.**

**Interactive flow:**
1. Enter search keyword
2. Enter number of videos to download (default 5)
3. Auto-read `cookie.txt` (prompts for input if not found)
4. Auto-validate cookie
5. Search videos and extract BV IDs
6. Dynamically allocate download processes, start concurrent download
7. Each video uses multi-thread chunked download, video+audio in parallel
8. Stage 2: Multi-process FFmpeg batch merge to MP4
9. Detailed summary at the end (success/fail list, total time)

**Dynamic process allocation rules:**

| Download Count | Processes | Description |
|----------------|-----------|-------------|
| 1~2 | 1 | Single process for small batches |
| 3~4 | 2 | Dual process |
| 5~8 | 3 | Triple process |
| 9~16 | 4 | Quad process |
| 16+ | Up to 8 | Dynamically adjusted based on CPU cores |

### Bangumi Spider (bsp.py)

**Supported URL formats:**
- `https://www.bilibili.com/bangumi/play/ssXXXXXX` (SS ID, season page)
- `https://www.bilibili.com/bangumi/play/epXXXXXX` (EP ID, single episode page)

**Interactive flow:**
1. Enter the bangumi URL
2. Auto-read `cookie.txt` (prompts for input if not found)
3. Auto-validate cookie
4. List available qualities, enter number to choose (Enter for highest quality)
5. Start download (video and audio downloaded separately)
6. After download, ask whether to merge (Y/n, default yes)
7. Original segmented files are auto-deleted after successful merge

### Regular Video Spider (bspv.py)

**Supported format:**
- Enter BV ID, e.g. `BV1GJ411x7h7`

**Interactive flow:**
1. Enter BV ID
2. Enter / read cookie
3. Validate cookie
4. List available qualities, manually enter quality ID
5. Choose whether to save video
6. Choose whether to save audio

---

## Output Structure

### Unified Output

All scripts' download results are stored in the `output/` directory:

```
output/
├── .tmp/                               # Temporary files (downloading)
│   ├── [2024-01-15] Video Title_video.m4s
│   └── [2024-01-15] Video Title_audio.m4a
├── video/                              # Video source files (optional)
│   └── Bangumi Name EP X: Title.m4s
├── audio/                              # Audio source files (optional)
│   └── Bangumi Name EP X: Title.m4a
├── [2024-01-15] Video Title 1.mp4       # Merged complete video
└── [2024-01-16] Video Title 2.mp4       # Merged complete video
```

### Logs & Results

```
log/
├── 20240115_153020_bsps.log            # Runtime log
├── 20240115_153020_bsps_result.txt     # Result file
├── 20240115_153100_bsp.log             # Bangumi spider log
└── 20240115_153200_batch_summary.txt   # Master controller batch summary
```

---

## Disclaimer / What You Cannot Do

### ⚠️ This tool **CANNOT** be used for the following purposes

1. **Commercial use**: Do not use for any commercial purpose, including but not limited to selling, profiting, or advertising
2. **Bulk scraping**: Do not scrape Bilibili resources at large scale or high frequency to avoid server load
3. **Piracy distribution**: Do not upload downloaded content to other platforms or share with others
4. **Bypassing membership limits**: Do not use to crack membership, paid content, or other technical restrictions
5. **Copyright infringement**: Downloaded content is for personal study and research use only. Please comply with copyright laws and regulations
6. **Malicious use**: Do not use for any illegal activities or actions that harm Bilibili or others' interests

### 📌 Terms of Use

- This tool is for **personal learning and research** use only
- Please consciously abide by the Copyright Law of the People's Republic of China and related laws and regulations
- Please comply with Bilibili's User Agreement and Community Guidelines
- Any consequences arising from the use of this tool shall be borne by the user
- The author is not responsible for any problems caused by using this tool

### 🔒 Privacy Statement

- This tool will never upload your cookie or any personal information
- All requests are sent directly to Bilibili official servers
- Log files are only stored locally and can be deleted at any time

---

## FAQ

### Q: Why does it say "playinfo JSON not found"?

A: Possible reasons:
- Incorrect URL — make sure it's a bangumi or video playback page
- Page structure has changed — script may need updating
- Network issues causing incomplete page load

### Q: Why is the downloaded quality so low?

A: Most likely your cookie is invalid or you're not logged in. Configure a valid login cookie and try again.

### Q: Merge failed. What should I do?

A:
1. Verify FFmpeg is installed and in PATH: `ffmpeg -version`
2. Try manually specifying the FFmpeg path (see [FFmpeg Setup](#ffmpeg-setup))
3. Original files will not be deleted — you can manually merge with other tools

### Q: Download got interrupted halfway. Can I resume?

A: Resumable downloads are not currently supported. You'll need to re-download. For large files, we recommend downloading when your network is stable.

> 💡 bsps.py's multi-thread chunked download doesn't support resume, but it's faster and less likely to be interrupted.

### Q: Does it support batch downloading?

A: Yes! Use the `bsps.py` search & batch download script, or `main.py`'s mixed batch task mode.

### Q: Does it support downloading danmaku (bullet comments) or subtitles?

A: No, only video and audio are downloaded.

### Q: What's the difference between the paired scripts?

A: Identical functionality, only difference is the amount of comments:
- `*.py` (e.g. `bsp.py`): minimal version, no extra comments, for daily use
- `* - 副本.py` (e.g. `bsps - 副本.py`): fully commented version, for learning the code

### Q: What's the test/ directory?

A: The test/ directory contains beta versions with the latest features, which may include experimental functionality. Use the scripts in the root directory for stable versions.

### Q: What's the difference between the master controller and running scripts individually?

A: The master controller `main.py` calls three scripts through subprocess. Benefits:
- Unified entry point, no need to remember different script names
- Cookie set once, used by all three scripts
- Supports mixed batch tasks (e.g. search then download, then download bangumi)
- Auto-generates summary reports

Running scripts individually is more straightforward, suitable when you only need one feature.

---

## Technical Details

### Core Principles

1. **Search parsing**: Request search page, extract BV IDs with regex and deduplicate
2. **Page request**: Simulate browser request to video/bangumi page
3. **Data extraction**: Extract `window.__playinfo__` or `playurlSSRData` JSON from HTML
4. **JSON parsing**: Use bracket depth matching method to accurately extract nested JSON
5. **Data normalization**: Unify different data structures between PGC bangumi and regular videos
6. **Quality selection**: Group and deduplicate by quality ID, sort from high to low
7. **Publish date**: Extract pubdate/ctime from playinfo, format as date string
8. **Chunked download**: Multi-threaded Range requests, merge from memory buffer
9. **Streaming download**: `stream=True` for chunked download with real-time progress
10. **Audio/video merge**: Call FFmpeg for lossless merge (`-c copy`)
11. **Multi-process concurrency**: `multiprocessing.Pool` pool, dynamically adjust workers
12. **Task scheduling**: Master controller auto-passes params via subprocess + stdin

### Dependencies

- `requests` — HTTP requests
- `json` — JSON parsing
- `re` — Regular expressions
- `logging` — Log recording
- `subprocess` / `ffmpeg-python` — FFmpeg invocation
- `shutil` — Executable path lookup
- `multiprocessing` — Multi-process concurrency
- `threading` + `concurrent.futures` — Multi-threaded chunked download
- `datetime` — Date and time handling
- `io` — In-memory byte stream (chunk buffer)

---

## License

MIT License

For learning and communication purposes only. Please comply with relevant laws, regulations, and Bilibili's User Agreement.
