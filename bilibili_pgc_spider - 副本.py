# ============================================================================
# B站番剧下载脚本 (PGC)
# 功能：下载B站番剧/电影/纪录片等PGC内容，支持画质选择
# 特点：自动解析播放地址、DASH音视频分离下载、FFmpeg自动合并
# ============================================================================

import json                     # JSON 数据解析
import os                       # 文件路径与目录操作
import re                       # 正则表达式
import sys                      # 系统参数（标准输出刷新）
import shutil                   # 文件操作（查找ffmpeg等）
import requests                 # HTTP 请求
import logging                  # 日志记录
import subprocess               # 子进程（调用ffmpeg）
from urllib.parse import urlparse  # URL 解析
from datetime import datetime   # 日期时间处理

# 可选依赖：ffmpeg-python
try:
    import ffmpeg
    _HAS_FFMPEG_PY = True
except ImportError:
    ffmpeg = None
    _HAS_FFMPEG_PY = False


# ============================================================================
# 基础配置
# ============================================================================

# 脚本所在目录的绝对路径
BASE_DIR = os.environ.get("BILI_BASE_DIR") or os.path.dirname(os.path.abspath(__file__))

# FFmpeg 可执行文件路径：优先环境变量，其次系统 PATH
FFMPEG_PATH = os.environ.get('FFMPEG_PATH', '') or shutil.which('ffmpeg') or ''

# 日志目录配置（每次运行生成独立日志）
LOG_DIR = os.path.join(BASE_DIR, "log")
os.makedirs(LOG_DIR, exist_ok=True)

_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
_script_name = os.path.splitext(os.path.basename(__file__))[0]
LOG_PATH = os.path.join(LOG_DIR, f"{_timestamp}_{_script_name}.log")
RESULT_PATH = os.path.join(LOG_DIR, f"{_timestamp}_{_script_name}_result.txt")

# 日志记录器初始化
logger = logging.getLogger("bili_pgc")
logger.setLevel(logging.INFO)
fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

# 控制台输出
ch = logging.StreamHandler()
ch.setFormatter(fmt)
logger.addHandler(ch)

# 文件输出（UTF-8 编码，每次运行新建）
fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
fh.setFormatter(fmt)
logger.addHandler(fh)


# ============================================================================
# 工具函数
# ============================================================================

def sanitize_filename(name, fallback="file"):
    """清理文件名中的非法字符，确保能在 Windows/Linux 上正常保存"""
    safe = os.path.basename(str(name)).strip()
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '', safe)  # 移除非法字符
    safe = re.sub(r'[.\s]+$', '', safe)  # 移除末尾的点和空格
    return safe or fallback


def cookie_str_to_dict(cookie_string):
    """将 cookie 字符串转换为字典格式，供 requests 使用"""
    return {pair.split('=', 1)[0]: pair.split('=', 1)[1]
            for pair in cookie_string.split('; ') if '=' in pair}


def validate_cookie(cookie_string, headers):
    """验证 Cookie 是否有效（是否已登录），返回 True/False"""
    if not cookie_string:
        logger.warning("没有提供 cookie，使用匿名请求。")
        return False
    check_headers = dict(headers)
    check_headers['Cookie'] = cookie_string
    try:
        # 调用 B 站导航接口验证登录状态
        r = requests.get('https://api.bilibili.com/x/web-interface/nav',
                         headers=check_headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get('data', {}).get('isLogin'):
            logger.info('Cookie 有效，已登录')
            return True
        logger.warning('Cookie 未登录或无效')
        return False
    except Exception as e:
        logger.warning(f'验证 cookie 时异常: {e}')
        return False


# ============================================================================
# 页面解析：提取播放信息
# ============================================================================

def extract_json_after_prefix(html, prefix):
    """从 HTML 中提取指定前缀后的 JSON 对象
    使用大括号深度匹配法，精准定位 JSON 边界"""
    idx = html.find(prefix)
    if idx == -1:
        return None
    start = idx + len(prefix)
    # 跳过前缀后的空白字符
    while start < len(html) and html[start] in ' \t\n\r':
        start += 1
    if start >= len(html) or html[start] != '{':
        return None

    # 大括号深度匹配
    depth = 0
    i = start
    in_string = False  # 是否在字符串内
    escape = False     # 是否处于转义状态
    while i < len(html):
        ch = html[i]
        if escape:
            escape = False
        elif ch == '\\':
            escape = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start:i+1])
                    except json.JSONDecodeError:
                        return None
        i += 1
    return None


def extract_playinfo_from_html(html):
    """从番剧页面 HTML 中提取播放信息（playinfo）
    支持多种前缀格式，按优先级尝试"""
    prefixes = [
        'const playurlSSRData =',
        'playurlSSRData =',
        'window.__PLAYURL_HYDRATE_DATA__ =',
        'window.__playinfo__ =',
        '__playinfo__ =',
    ]
    for p in prefixes:
        data = extract_json_after_prefix(html, p)
        if data:
            return data
    return None


def get_playinfo_normalized(parsed):
    """将不同格式的 playinfo 规范化为统一结构
    统一返回包含 dash 和/或 durl 的字典"""
    if not parsed:
        return None

    # 格式一：{ data: { result: { video_info: { dash, durl } } } }
    if isinstance(parsed.get('data'), dict):
        data = parsed['data']
        if isinstance(data.get('result'), dict):
            res = data['result']
            vi = res.get('video_info', {})
            has_dash = bool(res.get('dash')) or bool(vi.get('dash'))
            has_durl = bool(res.get('durl')) or bool(vi.get('durl'))
            if has_dash or has_durl:
                # 确保 dash 和 durl 字段在顶层
                if not res.get('dash') and vi.get('dash'):
                    res['dash'] = vi['dash']
                if not res.get('durl') and vi.get('durl'):
                    res['durl'] = vi['durl']
                return res
        # 格式二：{ data: { dash, durl } }
        if 'dash' in data or 'durl' in data:
            return data

    # 格式三：顶层直接有 dash/durl
    if 'dash' in parsed or 'durl' in parsed:
        return parsed

    # 格式四：{ result: {...} }
    if isinstance(parsed.get('result'), dict):
        return parsed['result']

    return None


# ============================================================================
# 画质选择
# ============================================================================

def list_qualities(playinfo):
    """列出所有可用画质，返回画质选项列表"""
    dash = playinfo.get('dash', {})
    vlist = dash.get('video', [])  # 视频流列表
    alist = dash.get('audio', [])  # 音频流列表
    qualities = []

    # DASH 格式画质
    if vlist:
        # 选择码率最高的音频作为默认音频
        best_a = max(alist, key=lambda x: x.get('bandwidth', 0)) if alist else None
        # 按画质ID分组，每组取码率最高的视频流
        quality_groups = {}
        for v in vlist:
            qid = v.get('id', v.get('quality', 0))
            if qid not in quality_groups or v.get('bandwidth', 0) > quality_groups[qid].get('bandwidth', 0):
                quality_groups[qid] = v
        # 画质从高到低排序
        sorted_qids = sorted(quality_groups.keys(), reverse=True)
        for qid in sorted_qids:
            v = quality_groups[qid]
            qualities.append({
                'type': 'dash',
                'video': v,
                'audio': best_a,
                'desc': f"{qid} ({v.get('width', '?')}x{v.get('height', '?')}) - DASH",
            })

    # durl 直链画质（旧格式）
    if playinfo.get('durl'):
        sorted_d = sorted(playinfo['durl'], key=lambda x: x.get('size', 0), reverse=True)
        for d in sorted_d:
            qualities.append({
                'type': 'durl',
                'durl': d,
                'desc': f"{playinfo.get('video_info', {}).get('quality', '?')} (durl直链)",
            })

    return qualities


def choose_quality(playinfo):
    """交互式选择画质，返回选中的视频/音频链接信息"""
    qlist = list_qualities(playinfo)
    if not qlist:
        return None

    print()
    print('可选画质：')
    for i, q in enumerate(qlist):
        print(f"  {i + 1}. {q['desc']}")

    choice = input(f'请选择画质（默认 1，最高画质）: ').strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(qlist):
            selected = qlist[idx]
        else:
            selected = qlist[0]
    except ValueError:
        selected = qlist[0]

    # 根据类型返回对应的链接信息
    if selected['type'] == 'dash':
        v = selected['video']
        a = selected['audio']
        return {
            'video_url': v.get('baseUrl') or v.get('base_url'),
            'audio_url': a.get('baseUrl') or a.get('base_url') if a else None,
            'quality': selected['desc'],
        }
    else:
        d = selected['durl']
        return {
            'video_url': d.get('url') or d.get('backup_url'),
            'audio_url': None,
            'quality': selected['desc'],
        }


# ============================================================================
# 标题提取
# ============================================================================

def extract_title(html, playinfo):
    """从页面和 playinfo 中提取番剧标题
    优先使用番剧名+集数的组合格式"""
    sup = playinfo.get('supplement', {}) if playinfo else {}
    ep = sup.get('ogv_episode_info', {})

    # 尝试从 og:title 元标签获取番剧名
    og_title = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
    season = og_title.group(1).strip() if og_title else ''

    # 组合标题：番剧名 + 集数 + 标题
    if season and ep.get('index_title') and ep.get('long_title'):
        return f"{season} 第{ep['index_title']}集：{ep['long_title']}"
    if season and ep.get('index_title'):
        return f"{season} 第{ep['index_title']}集"
    if season:
        return season

    # 备用方案：从 title 标签或 meta 标签提取
    for pat in [r'<title>(.*?)</title>', r'<meta\s+itemProp="name"\s+content="([^"]+)"']:
        m = re.search(pat, html)
        if m:
            t = m.group(1).strip()
            for sep in ['-番剧-', '_哔哩哔哩_bilibili', '-bilibili']:
                if sep in t:
                    t = t.split(sep)[0].strip()
            if t:
                return t
    return urlparse(html).path.replace('/', '_') if html else 'video'


# ============================================================================
# 下载功能
# ============================================================================

def download_stream(url, path, headers=None, cookies=None):
    """流式下载文件，带动画进度条

    功能特性：
    - 已知文件大小：显示流动进度条 + 百分比 + 文件大小
    - 未知文件大小：显示旋转 spinner + 已下载大小
    - 进度条动画：箭头在 '>' → '»' → '➤' 之间切换，产生流动效果
    - 自适应单位：文件 ≥ 1024 KB 时自动显示 MB，否则显示 KB

    Args:
        url: 下载链接
        path: 本地保存路径
        headers: 请求头字典，默认为空
        cookies: Cookie 字典，默认为空
    """
    headers = headers or {}
    cookies = cookies or {}
    fname = os.path.basename(path)
    logger.info(f"开始下载 -> {fname}")
    with requests.get(url, headers=headers, cookies=cookies, stream=True, timeout=60) as r:
        r.raise_for_status()
        # 获取文件总大小（服务器可能不提供）
        total = int(r.headers.get('content-length', 0))
        downloaded = 0
        # 进度条宽度（字符数）
        bar_width = 40
        # 动画相位计数器，用于切换箭头样式产生流动效果
        phase = 0
        with open(path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        # ===== 已知总大小：显示进度条 =====
                        pct = downloaded / total
                        filled = int(bar_width * pct)
                        # 每接收一个 chunk，相位前进 1/6 周期
                        phase = (phase + 1) % 6
                        if filled < bar_width:
                            # 根据相位切换箭头样式：'>' → '»' → '➤'，产生流动感
                            arrow = '>' if phase < 3 else '»' if phase < 5 else '➤'
                            bar = '=' * filled + arrow + ' ' * (bar_width - filled - 1)
                        else:
                            # 进度满格后箭头消失，全显示 '='
                            bar = '=' * bar_width
                        cur_kb = downloaded // 1024
                        total_kb = total // 1024
                        # 自适应单位：大于等于 1024 KB 显示 MB
                        if total_kb >= 1024:
                            cur_mb = downloaded / 1024 / 1024
                            total_mb = total / 1024 / 1024
                            size_str = f"{cur_mb:.1f}/{total_mb:.1f} MB"
                        else:
                            size_str = f"{cur_kb}/{total_kb} KB"
                        # \r 回到行首，实现单行刷新；百分比右对齐 5 字符宽度
                        sys.stdout.write(f"\r  [{bar}] {pct*100:5.1f}%  {size_str}")
                        sys.stdout.flush()
                    else:
                        # ===== 未知总大小：显示旋转 spinner =====
                        # 每接收一个 chunk，相位前进 1/8 周期
                        phase = (phase + 1) % 8
                        # 4 种旋转状态：'|' → '/' → '—' → '\\'
                        spinner = '|/—\\'[phase % 4]
                        cur_kb = downloaded // 1024
                        sys.stdout.write(f"\r  {spinner} 下载中... {cur_kb} KB")
                        sys.stdout.flush()
    # 下载完成换行，避免后续输出接在进度条后面
    print()
    logger.info(f"下载完成: {path}")


# ============================================================================
# FFmpeg 音视频合并
# ============================================================================

def have_ffmpeg():
    """检测系统中是否有可用的 FFmpeg"""
    if FFMPEG_PATH and os.path.isfile(FFMPEG_PATH):
        return True
    if _HAS_FFMPEG_PY:
        try:
            ffmpeg.probe('__nonexistent__')
        except ffmpeg.Error:
            return True
        except Exception:
            pass
    return bool(shutil.which('ffmpeg'))


def merge_av(video_path, audio_path, out_path):
    """使用 FFmpeg 合并视频和音频为一个 MP4 文件
    优先使用 ffmpeg-python，其次使用命令行"""
    if not have_ffmpeg():
        return False
    logger.info('ffmpeg 合并音视频...')
    ffmpeg_exe = FFMPEG_PATH or 'ffmpeg'
    use_py = _HAS_FFMPEG_PY and not FFMPEG_PATH
    try:
        if use_py:
            # 使用 ffmpeg-python 库
            input_v = ffmpeg.input(video_path)
            if audio_path:
                input_a = ffmpeg.input(audio_path)
                ffmpeg.output(input_v, input_a, out_path, c='copy').run(
                    overwrite_output=True, quiet=True
                )
            else:
                ffmpeg.output(input_v, out_path, c='copy').run(
                    overwrite_output=True, quiet=True
                )
        else:
            # 使用命令行调用 FFmpeg（流拷贝，不重新编码）
            cmd = [ffmpeg_exe, '-y', '-i', video_path]
            if audio_path:
                cmd += ['-i', audio_path, '-c', 'copy']
            else:
                cmd += ['-c', 'copy']
            cmd.append(out_path)
            subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f'合并完成: {out_path}')
        return True
    except Exception as e:
        logger.error(f'ffmpeg 合并失败: {e}')
        return False


# ============================================================================
# 主函数：交互式入口
# ============================================================================

def main():
    # 1. 输入番剧 URL
    url = input('请输入B站番剧URL: ').strip()
    if not url:
        logger.error('未提供 URL')
        return

    # 2. 读取 Cookie（优先从 cookie.txt 读取）
    cookie_string = ''
    cf = os.path.join(BASE_DIR, 'cookie.txt')
    if os.path.exists(cf):
        with open(cf, 'r', encoding='utf-8') as f:
            cookie_string = f.read().strip()
    if not cookie_string:
        cookie_string = input('请粘贴 cookie（回车跳过）: ').strip()

    # 3. 输出目录
    out_dir = os.path.join(BASE_DIR, 'output')

    # 4. 请求头配置
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/130.0.0.0 Safari/537.36',
        'Referer': url,
    }

    # 5. 验证 Cookie
    validate_cookie(cookie_string, headers)
    cookies = cookie_str_to_dict(cookie_string) if cookie_string else None

    # 6. 请求番剧页面
    try:
        r = requests.get(url, headers=headers, cookies=cookies, timeout=20)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        logger.error(f'请求页面失败: {e}')
        return

    # 7. 解析播放信息
    parsed = extract_playinfo_from_html(html)
    if not parsed:
        logger.error('未找到 playinfo JSON')
        return

    playinfo = get_playinfo_normalized(parsed)
    if not playinfo:
        logger.error('解析不到 dash/durl 信息')
        return

    # 8. 选择画质
    pick = choose_quality(playinfo)
    if not pick:
        logger.error('未找到可下载的媒体链接')
        return

    # 9. 准备输出目录
    os.makedirs(out_dir, exist_ok=True)
    video_dir = os.path.join(out_dir, 'video')
    audio_dir = os.path.join(out_dir, 'audio')
    os.makedirs(video_dir, exist_ok=True)
    os.makedirs(audio_dir, exist_ok=True)

    # 10. 提取标题
    title = sanitize_filename(extract_title(html, playinfo))
    logger.info(f'标题: {title}')
    logger.info(f'画质: {pick["quality"]}')

    video_url = pick.get('video_url')
    audio_url = pick.get('audio_url')
    video_path = audio_path = None

    # 11. 下载视频
    if video_url:
        video_ext = os.path.splitext(urlparse(video_url).path)[1] or '.mp4'
        video_path = os.path.join(video_dir, title + video_ext)
        download_stream(video_url, video_path, headers=headers, cookies=cookies)

    # 12. 下载音频
    if audio_url:
        audio_ext = os.path.splitext(urlparse(audio_url).path)[1] or '.m4a'
        audio_path = os.path.join(audio_dir, title + audio_ext)
        download_stream(audio_url, audio_path, headers=headers, cookies=cookies)

    # 13. 合并音视频
    if video_path and audio_path:
        merged = os.path.join(out_dir, title + '.mp4')
        print()
        do_merge = input('是否使用 FFmpeg 合并音视频为一个 MP4 文件？(Y/n): ').strip().lower()
        if do_merge in ('', 'y', 'yes'):
            if merge_av(video_path, audio_path, merged):
                # 合并成功，删除原始文件
                try:
                    os.remove(video_path)
                    os.remove(audio_path)
                except Exception:
                    pass
            else:
                logger.info('合并失败，原始文件保留在 video/ 和 audio/ 子目录')
        else:
            logger.info('跳过合并，视频在 video/ 目录，音频在 audio/ 目录')
    elif video_path:
        logger.info('完成: ' + video_path)

    # 14. 写入结果文件
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        f.write("===== 下载结果 =====\n")
        f.write(f"标题: {title}\n")
        f.write(f"画质: {pick['quality']}\n")
        if video_path:
            f.write(f"视频文件: {video_path}\n")
        if audio_path:
            f.write(f"音频文件: {audio_path}\n")
        if video_path and audio_path and os.path.exists(merged):
            f.write(f"合并文件: {merged}\n")
            f.write("状态: 已合并\n")
        elif video_path and audio_path:
            f.write("状态: 未合并\n")
        elif video_path:
            f.write("状态: 完成（单视频）\n")
    logger.info(f"结果已保存到：{RESULT_PATH}")


# ============================================================================
# 程序入口
# ============================================================================

if __name__ == '__main__':
    main()
    input('按回车退出...')
