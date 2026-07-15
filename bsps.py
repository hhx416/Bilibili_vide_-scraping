import json
import os
import re
import sys
import io
import time
import shutil
import requests
import logging
import subprocess
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, quote
from datetime import datetime

try:
    import ffmpeg
    _HAS_FFMPEG_PY = True
except ImportError:
    ffmpeg = None
    _HAS_FFMPEG_PY = False


BASE_DIR = os.environ.get("BILI_BASE_DIR") or os.path.dirname(os.path.abspath(__file__))

FFMPEG_PATH = os.environ.get('FFMPEG_PATH', '') or shutil.which('ffmpeg') or ''

LOG_DIR = os.path.join(BASE_DIR, "log")
os.makedirs(LOG_DIR, exist_ok=True)

_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
_script_name = os.path.splitext(os.path.basename(__file__))[0]
LOG_PATH = os.path.join(LOG_DIR, f"{_timestamp}_{_script_name}.log")
RESULT_PATH = os.path.join(LOG_DIR, f"{_timestamp}_{_script_name}_result.txt")

MAX_CHUNK_THREADS = 8
MIN_CHUNK_SIZE = 1 * 1024 * 1024
MERGE_WORKERS = 8

_log_lock = threading.Lock()


def _setup_logger():
    lg = logging.getLogger("bili_spider")
    lg.setLevel(logging.INFO)
    if lg.handlers:
        return lg
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    lg.addHandler(ch)
    fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    fh.setFormatter(fmt)
    lg.addHandler(fh)
    return lg


logger = _setup_logger()


def log_msg(msg, level=logging.INFO):
    with _log_lock:
        logger.log(level, msg)


def sanitize_filename(name, fallback="file"):
    safe = os.path.basename(str(name)).strip()
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '', safe)
    safe = re.sub(r'[.\s]+$', '', safe)
    return safe or fallback


def cookie_str_to_dict(cookie_string):
    return {pair.split('=', 1)[0]: pair.split('=', 1)[1] for pair in cookie_string.split('; ') if '=' in pair}


def validate_cookie(cookie_string, headers):
    if not cookie_string:
        log_msg("没有提供 cookie，使用匿名请求。", logging.WARNING)
        return False
    check_headers = dict(headers)
    check_headers['Cookie'] = cookie_string
    try:
        r = requests.get('https://api.bilibili.com/x/web-interface/nav', headers=check_headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get('data', {}).get('isLogin'):
            log_msg('Cookie 有效，已登录')
            return True
        log_msg('Cookie 未登录或无效', logging.WARNING)
        return False
    except Exception as e:
        log_msg(f'验证 cookie 时异常: {e}', logging.WARNING)
        return False


def search_bilibili(keyword, headers, cookies=None, max_results=50):
    log_msg(f"搜索关键词: {keyword}")
    search_url = f"https://search.bilibili.com/all?keyword={quote(keyword)}"
    search_headers = dict(headers)
    search_headers['Referer'] = 'https://www.bilibili.com/'
    try:
        r = requests.get(search_url, headers=search_headers, cookies=cookies, timeout=20)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        log_msg(f'搜索请求失败: {e}', logging.ERROR)
        return []

    bv_pattern = r'//www\.bilibili\.com/video/(BV[0-9a-zA-Z]{10})'
    matches = re.findall(bv_pattern, html)
    seen = set()
    bv_list = []
    for bv in matches:
        if bv not in seen:
            seen.add(bv)
            bv_list.append(bv)

    result = bv_list[:max_results]
    log_msg(f"搜索到 {len(bv_list)} 个视频，取前 {len(result)} 个")
    return result


def extract_playinfo_from_html(html):
    prefixes = [
        'window.__playinfo__ =',
        'window.__playinfo__=',
        '__playinfo__ =',
    ]
    for p in prefixes:
        idx = html.find(p)
        if idx == -1:
            continue
        start = idx + len(p)
        while start < len(html) and html[start] in ' \t\n\r=':
            start += 1
        if start >= len(html) or html[start] != '{':
            continue
        depth = 0
        i = start
        in_string = False
        escape = False
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


def get_best_quality(playinfo):
    data = playinfo.get('data', playinfo)
    dash = data.get('dash', {})
    vlist = dash.get('video', [])
    alist = dash.get('audio', [])
    if not vlist:
        durl = data.get('durl')
        if durl:
            best = max(durl, key=lambda x: x.get('size', 0))
            return {
                'type': 'durl',
                'video_url': best.get('url') or best.get('backup_url'),
                'audio_url': None,
                'quality': 'durl直链',
                'video_size': best.get('size', 0),
            }
        return None

    quality_groups = {}
    for v in vlist:
        qid = v.get('id', v.get('quality', 0))
        if qid not in quality_groups or v.get('bandwidth', 0) > quality_groups[qid].get('bandwidth', 0):
            quality_groups[qid] = v
    best_qid = max(quality_groups.keys())
    best_v = quality_groups[best_qid]
    best_a = max(alist, key=lambda x: x.get('bandwidth', 0)) if alist else None
    return {
        'type': 'dash',
        'video_url': best_v.get('baseUrl') or best_v.get('base_url'),
        'audio_url': best_a.get('baseUrl') or best_a.get('base_url') if best_a else None,
        'quality': f"{best_qid} ({best_v.get('width', '?')}x{best_v.get('height', '?')})",
        'video_size': best_v.get('size', 0),
    }


def extract_title_from_html(html):
    m = re.search(r'<title>(.*?)</title>', html)
    if m:
        t = m.group(1).strip()
        for sep in ['_哔哩哔哩_bilibili', '-bilibili', '哔哩哔哩']:
            if sep in t:
                t = t.split(sep)[0].strip()
        if t:
            return t
    return 'video'


def extract_pubdate(playinfo, html):
    data = playinfo.get('data', playinfo)
    vi = data.get('video_info', {})
    pubdate = vi.get('pubdate') or vi.get('ctime') or data.get('pubdate') or data.get('ctime')
    if pubdate:
        try:
            dt = datetime.fromtimestamp(int(pubdate))
            return dt.strftime('%Y-%m-%d')
        except Exception:
            pass
    m = re.search(r'"pubdate"\s*:\s*(\d+)', html)
    if m:
        try:
            dt = datetime.fromtimestamp(int(m.group(1)))
            return dt.strftime('%Y-%m-%d')
        except Exception:
            pass
    return None


def _get_file_size(url, headers, cookies):
    try:
        r = requests.head(url, headers=headers, cookies=cookies, timeout=15, allow_redirects=True)
        if r.status_code in (200, 206):
            size = int(r.headers.get('content-length', 0))
            accept_ranges = r.headers.get('accept-ranges', '').lower() == 'bytes'
            return size, accept_ranges
    except Exception:
        pass
    try:
        r = requests.get(url, headers={**headers, 'Range': 'bytes=0-0'}, cookies=cookies, timeout=15, stream=True)
        if r.status_code == 206:
            cr = r.headers.get('content-range', '')
            m = re.search(r'/(\d+)$', cr)
            if m:
                return int(m.group(1)), True
            return 0, True
    except Exception:
        pass
    return 0, False


def _download_range(url, start, end, headers, cookies, buf_list, idx, progress_dict, total_size, prefix):
    range_headers = dict(headers)
    range_headers['Range'] = f'bytes={start}-{end}'
    downloaded = 0
    try:
        with requests.get(url, headers=range_headers, cookies=cookies, stream=True, timeout=60) as r:
            r.raise_for_status()
            buf = io.BytesIO()
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    buf.write(chunk)
                    downloaded += len(chunk)
                    if progress_dict is not None:
                        with progress_dict['lock']:
                            progress_dict['bytes'] += len(chunk)
                            cur = progress_dict['bytes']
                            if total_size and progress_dict.get('last_print', 0) != cur // (1024 * 1024):
                                progress_dict['last_print'] = cur // (1024 * 1024)
                                pct = cur / total_size * 100
                                sys.stdout.write(f"\r  {prefix}进度: {pct:.1f}% ({cur//1024//1024}/{total_size//1024//1024} MB)")
                                sys.stdout.flush()
            buf_list[idx] = buf
        return downloaded
    except Exception as e:
        buf_list[idx] = None
        raise e


def download_file_chunked(url, out_path, headers=None, cookies=None, prefix=""):
    headers = headers or {}
    cookies = cookies or {}
    fname = os.path.basename(out_path)
    log_msg(f"{prefix}开始下载 -> {fname} (多线程分片)")

    total_size, supports_range = _get_file_size(url, headers, cookies)

    if not supports_range or total_size < MIN_CHUNK_SIZE * 2:
        downloaded = 0
        with requests.get(url, headers=headers, cookies=cookies, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(out_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            pct = downloaded / total_size * 100
                            sys.stdout.write(f"\r  {prefix}进度: {pct:.1f}% ({downloaded//1024//1024}/{total_size//1024//1024} MB)")
                            sys.stdout.flush()
        print()
        log_msg(f"{prefix}下载完成: {fname} ({downloaded//1024//1024} MB)")
        return downloaded

    num_chunks = min(MAX_CHUNK_THREADS, max(2, total_size // (5 * 1024 * 1024)))
    chunk_size = total_size // num_chunks
    chunks = []
    for i in range(num_chunks):
        start = i * chunk_size
        end = start + chunk_size - 1 if i < num_chunks - 1 else total_size - 1
        chunks.append((start, end))

    log_msg(f"{prefix}分片数: {num_chunks}, 单分片大小: ~{chunk_size//1024//1024} MB")

    buf_list = [None] * num_chunks
    progress_dict = {'bytes': 0, 'lock': threading.Lock(), 'last_print': -1}

    with ThreadPoolExecutor(max_workers=num_chunks) as executor:
        futures = []
        for i, (start, end) in enumerate(chunks):
            futures.append(executor.submit(
                _download_range, url, start, end, headers, cookies, buf_list, i,
                progress_dict, total_size, prefix
            ))
        for f in as_completed(futures):
            f.result()

    print()

    with open(out_path, 'wb') as f:
        for b in buf_list:
            if b:
                b.seek(0)
                f.write(b.read())

    log_msg(f"{prefix}下载完成: {fname} ({total_size//1024//1024} MB)")
    return total_size


def have_ffmpeg():
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


def merge_av_file(video_path, audio_path, out_path, cleanup=True):
    if not have_ffmpeg():
        return False
    ffmpeg_exe = FFMPEG_PATH or 'ffmpeg'
    try:
        cmd = [ffmpeg_exe, '-y', '-i', video_path]
        if audio_path:
            cmd += ['-i', audio_path, '-c', 'copy']
        else:
            cmd += ['-c', 'copy']
        cmd.append(out_path)
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        if cleanup:
            try:
                os.remove(video_path)
                if audio_path:
                    os.remove(audio_path)
            except Exception:
                pass
        return True
    except Exception:
        return False


def merge_worker_task(task):
    video_path = task.get('video_path')
    audio_path = task.get('audio_path')
    out_path = task.get('out_path')
    bv = task.get('bv')
    title = task.get('title')

    if not video_path or not os.path.exists(video_path):
        return {'bv': bv, 'title': title, 'success': False, 'error': '视频文件不存在'}

    if audio_path and not os.path.exists(audio_path):
        audio_path = None

    if audio_path and have_ffmpeg():
        ok = merge_av_file(video_path, audio_path, out_path, cleanup=True)
        if ok:
            return {'bv': bv, 'title': title, 'success': True, 'path': out_path}
        else:
            return {'bv': bv, 'title': title, 'success': True, 'path': video_path, 'merged': False}
    else:
        if os.path.dirname(video_path) != os.path.dirname(out_path):
            shutil.move(video_path, out_path)
        else:
            try:
                os.rename(video_path, out_path)
            except Exception:
                shutil.move(video_path, out_path)
        return {'bv': bv, 'title': title, 'success': True, 'path': out_path}


def download_single_video(bv, out_dir, headers_base, cookie_string, worker_id=0):
    prefix = f"[下载{worker_id}] "
    video_url = f"https://www.bilibili.com/video/{bv}"
    headers = dict(headers_base)
    headers['Referer'] = video_url
    cookies = cookie_str_to_dict(cookie_string) if cookie_string else None

    log_msg(f"{prefix}处理视频: {bv}")

    try:
        time.sleep(1)
        r = requests.get(video_url, headers=headers, cookies=cookies, timeout=20)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        log_msg(f'{prefix}请求页面失败 {bv}: {e}', logging.ERROR)
        return {'bv': bv, 'success': False, 'error': str(e), 'merge_task': None}

    playinfo = extract_playinfo_from_html(html)
    if not playinfo:
        log_msg(f'{prefix}未找到 playinfo JSON: {bv}', logging.ERROR)
        return {'bv': bv, 'success': False, 'error': '未找到 playinfo', 'merge_task': None}

    best = get_best_quality(playinfo)
    if not best:
        log_msg(f'{prefix}未找到可下载的媒体: {bv}', logging.ERROR)
        return {'bv': bv, 'success': False, 'error': '无可下载媒体', 'merge_task': None}

    title = sanitize_filename(extract_title_from_html(html))
    pubdate = extract_pubdate(playinfo, html)
    if pubdate:
        file_title = f"[{pubdate}] {title}"
    else:
        file_title = title
    log_msg(f"{prefix}标题: {title}")
    if pubdate:
        log_msg(f"{prefix}发布时间: {pubdate}")
    log_msg(f"{prefix}画质: {best['quality']}")

    video_url_dl = best.get('video_url')
    audio_url_dl = best.get('audio_url')
    video_path = audio_path = None

    tmp_dir = os.path.join(out_dir, '.tmp')
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        if video_url_dl and audio_url_dl:
            video_path = os.path.join(tmp_dir, f"{file_title}_video.m4s")
            audio_path = os.path.join(tmp_dir, f"{file_title}_audio.m4a")

            with ThreadPoolExecutor(max_workers=2) as pool:
                fv = pool.submit(
                    download_file_chunked, video_url_dl, video_path, headers, cookies, f"{prefix}[视频] "
                )
                fa = pool.submit(
                    download_file_chunked, audio_url_dl, audio_path, headers, cookies, f"{prefix}[音频] "
                )
                fv.result()
                fa.result()

        elif video_url_dl:
            video_path = os.path.join(tmp_dir, f"{file_title}_video.mp4")
            download_file_chunked(
                video_url_dl, video_path, headers, cookies, f"{prefix}[视频] "
            )

    except Exception as e:
        log_msg(f'{prefix}下载失败 {bv}: {e}', logging.ERROR)
        return {'bv': bv, 'title': title, 'pubdate': pubdate, 'success': False,
                'error': f'下载失败: {e}', 'merge_task': None}

    merged_path = os.path.join(out_dir, file_title + '.mp4')

    merge_task = {
        'video_path': video_path,
        'audio_path': audio_path,
        'out_path': merged_path,
        'bv': bv,
        'title': title,
    }

    log_msg(f"{prefix}下载完成，待合并: {file_title}")

    return {'bv': bv, 'title': title, 'pubdate': pubdate, 'success': True,
            'path': merged_path, 'quality': best['quality'], 'merge_task': merge_task}


def download_worker_task(worker_id, bv_batch, out_dir, headers, cookie_string):
    log_msg(f"===== 下载进程 {worker_id} 启动，负责 {len(bv_batch)} 个视频 =====")
    results = []
    for i, bv in enumerate(bv_batch):
        log_msg(f"[下载{worker_id}] 第 {i+1}/{len(bv_batch)} 个视频")
        result = download_single_video(bv, out_dir, headers, cookie_string, worker_id)
        results.append(result)
        if i < len(bv_batch) - 1:
            time.sleep(2)

    success_count = sum(1 for r in results if r.get('success'))
    log_msg(f"===== 下载进程 {worker_id} 完成，成功 {success_count}/{len(results)} =====")
    return results


def calc_dynamic_workers(total_count):
    cpu_count = multiprocessing.cpu_count()
    max_workers = max(1, cpu_count - 1)

    if total_count <= 2:
        return 1
    elif total_count <= 4:
        return min(2, max_workers)
    elif total_count <= 8:
        return min(3, max_workers)
    elif total_count <= 16:
        return min(4, max_workers)
    else:
        return min(max_workers, 8)


def main():
    keyword = input('请输入搜索关键词: ').strip()
    if not keyword:
        log_msg('未提供关键词', logging.ERROR)
        return

    count_str = input('请输入下载数量（默认5）: ').strip()
    try:
        count = int(count_str) if count_str else 5
    except ValueError:
        count = 5
    if count <= 0:
        count = 5

    cookie_string = ''
    cf = os.path.join(BASE_DIR, 'cookie.txt')
    if os.path.exists(cf):
        with open(cf, 'r', encoding='utf-8') as f:
            cookie_string = f.read().strip()
    if not cookie_string:
        cookie_string = input('请粘贴 cookie（回车跳过）: ').strip()

    out_dir = os.path.join(BASE_DIR, 'output')
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, 'video'), exist_ok=True)
    os.makedirs(os.path.join(out_dir, 'audio'), exist_ok=True)
    os.makedirs(os.path.join(out_dir, '.tmp'), exist_ok=True)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    }

    log_msg("===== B站搜索爬虫任务开始 =====")
    log_msg(f"搜索关键词: {keyword}")
    log_msg(f"下载数量: {count}")

    validate_cookie(cookie_string, headers)

    bv_list = search_bilibili(keyword, headers, cookie_str_to_dict(cookie_string) if cookie_string else None, count)
    if not bv_list:
        log_msg('未搜索到视频', logging.ERROR)
        return

    actual_count = min(count, len(bv_list))
    bv_list = bv_list[:actual_count]
    log_msg(f"将下载 {actual_count} 个视频")

    if have_ffmpeg():
        log_msg('FFmpeg 可用，下载完成后自动合并音视频')
    else:
        log_msg('未检测到 FFmpeg，将只下载不合并', logging.WARNING)

    num_download_workers = calc_dynamic_workers(actual_count)
    per_worker = max(1, (actual_count + num_download_workers - 1) // num_download_workers)
    log_msg(f"阶段一[下载]: 动态分配 {num_download_workers} 个下载进程，每进程约 {per_worker} 个视频 (CPU核心: {multiprocessing.cpu_count()})")

    batches = []
    for i in range(num_download_workers):
        batch = bv_list[i * per_worker:(i + 1) * per_worker]
        if batch:
            batches.append((i, batch))

    start_time = time.time()

    log_msg("===== 阶段一：开始下载 =====")

    if num_download_workers == 1:
        all_results = download_worker_task(0, bv_list, out_dir, headers, cookie_string)
    else:
        pool = multiprocessing.Pool(processes=num_download_workers)
        async_results = []
        for wid, batch in batches:
            async_results.append(pool.apply_async(download_worker_task, args=(wid, batch, out_dir, headers, cookie_string)))
        pool.close()
        pool.join()
        all_results = []
        for ar in async_results:
            all_results.extend(ar.get())

    download_elapsed = time.time() - start_time
    log_msg(f"===== 阶段一完成：下载耗时 {download_elapsed:.1f} 秒 =====")

    download_success = [r for r in all_results if r.get('success')]
    download_fail = [r for r in all_results if not r.get('success')]
    log_msg(f"下载成功: {len(download_success)} 个, 下载失败: {len(download_fail)} 个")

    merge_tasks = [r['merge_task'] for r in all_results if r.get('success') and r.get('merge_task')]

    if not merge_tasks:
        log_msg('没有需要合并的任务')
    else:
        num_merge_workers = min(MERGE_WORKERS, len(merge_tasks))
        log_msg(f"阶段二[合并]: 启动 {num_merge_workers} 个合并进程（共 {len(merge_tasks)} 个合并任务）")

        log_msg("===== 阶段二：开始合并 =====")
        merge_start = time.time()

        if num_merge_workers == 1:
            merge_results = [merge_worker_task(t) for t in merge_tasks]
        else:
            merge_pool = multiprocessing.Pool(processes=num_merge_workers)
            merge_results = merge_pool.map(merge_worker_task, merge_tasks)
            merge_pool.close()
            merge_pool.join()

        merge_elapsed = time.time() - merge_start
        log_msg(f"===== 阶段二完成：合并耗时 {merge_elapsed:.1f} 秒 =====")

        merge_success = [r for r in merge_results if r.get('success') and r.get('path') and os.path.exists(r.get('path'))]
        for r in download_success:
            for mr in merge_results:
                if mr.get('bv') == r.get('bv'):
                    r['merged'] = mr.get('success', False) and not mr.get('merged', False) == False
                    if not mr.get('success'):
                        r['merge_error'] = mr.get('error')
                    break

    tmp_dir = os.path.join(out_dir, '.tmp')
    try:
        if os.path.exists(tmp_dir) and not os.listdir(tmp_dir):
            os.rmdir(tmp_dir)
    except Exception:
        pass

    elapsed = time.time() - start_time

    success_list = [r for r in all_results if r.get('success')]
    fail_list = [r for r in all_results if not r.get('success')]

    print()
    log_msg("===== 任务全部执行完毕 =====")
    log_msg(f"总耗时: {elapsed:.1f} 秒 (下载: {download_elapsed:.1f}s)")
    log_msg(f"成功: {len(success_list)} 个")
    log_msg(f"失败: {len(fail_list)} 个")

    if success_list:
        log_msg("成功列表:")
        for r in success_list:
            log_msg(f"  ✓ {r.get('bv', '?')} - {r.get('title', '?')} [{r.get('quality', '?')}]")

    if fail_list:
        log_msg("失败列表:")
        for r in fail_list:
            log_msg(f"  ✗ {r.get('bv', '?')} - {r.get('error', '未知错误')}")

    log_msg(f"输出目录: {out_dir}")

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        f.write("===== 下载结果 =====\n")
        f.write(f"总耗时: {elapsed:.1f} 秒\n")
        f.write(f"成功: {len(success_list)} 个\n")
        f.write(f"失败: {len(fail_list)} 个\n")
        f.write(f"输出目录: {out_dir}\n\n")
        if success_list:
            f.write("===== 成功列表 =====\n")
            for r in success_list:
                f.write(f"  ✓ {r.get('bv', '?')} - {r.get('title', '?')} [{r.get('quality', '?')}]\n")
            f.write("\n")
        if fail_list:
            f.write("===== 失败列表 =====\n")
            for r in fail_list:
                f.write(f"  ✗ {r.get('bv', '?')} - {r.get('error', '未知错误')}\n")
    log_msg(f"结果已保存到：{RESULT_PATH}")


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
    input('按回车退出...')
