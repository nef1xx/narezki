"""Local-only HTTP app, no third-party Python packages required."""
import argparse
import base64
import struct
import hashlib
import json
import mimetypes
import os
import random
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from engine import ROOT, CREATE_FLAGS, available_encoders, binary, clip_plan, command, hook_range, probe, run, validate_settings

DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
(DATA / 'frames').mkdir(exist_ok=True)
CONFIG = DATA / 'settings.json'
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
PICKER_LOCK = threading.Lock()
FRAME_LOCK = threading.Semaphore(2)
MEDIA = {}
JOBS = {}
DEFAULT = {'settings': validate_settings({}), 'banner_path': '', 'hook_paths': [], 'output_dir': str(ROOT / 'exports')}
try:
    CONFIG_DATA = {**DEFAULT, **json.loads(CONFIG.read_text(encoding='utf-8'))}
except (OSError, ValueError):
    CONFIG_DATA = DEFAULT.copy()
# Migrate the former single bait file without losing it.
if not CONFIG_DATA.get('hook_paths') and CONFIG_DATA.get('hook_path'):
    CONFIG_DATA['hook_paths'] = [CONFIG_DATA['hook_path']]
CONFIG_DATA['hook_paths'] = list(dict.fromkeys(CONFIG_DATA.get('hook_paths') or []))

def save_config():
    with LOCK:
        temporary = CONFIG.with_suffix('.tmp')
        temporary.write_text(json.dumps(CONFIG_DATA, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(CONFIG)

def register(path):
    path = Path(str(path).strip().strip('"')).expanduser().resolve()
    if not path.is_file():
        raise ValueError('Файл не найден. Укажите полный путь к видео.')
    info = probe(path)
    key = hashlib.sha256(f'{path}:{path.stat().st_mtime_ns}:{path.stat().st_size}'.encode()).hexdigest()[:24]
    info['id'] = key
    with LOCK:
        MEDIA[key] = info
    return info

def public_job(job):
    return {k:v for k,v in job.items() if not k.startswith('_')}

def bait_schedule(hooks, count, rng=None):
    """Make shuffled bait/skip cycles, avoiding the same bait twice in a row."""
    if not hooks or count <= 0:
        return [None] * max(0, count)
    rng = rng or random.SystemRandom()
    skips = max(1, round(len(hooks) ** .5))
    result, previous = [], None
    while len(result) < count:
        cycle = list(hooks) + [None] * skips
        rng.shuffle(cycle)
        if previous is not None and cycle and cycle[0] is previous:
            swap = next((i for i, item in enumerate(cycle[1:], 1) if item is not previous), None)
            if swap is not None:
                cycle[0], cycle[swap] = cycle[swap], cycle[0]
        result.extend(cycle)
        previous = next((item for item in reversed(cycle) if item is not None), previous)
    return result[:count]

def render_job(job, source, ad, hooks, settings, preview, plan, schedule):
    partial = None
    try:
        durations = [(hook_range(hook, settings)[1] if hook else 0) for hook in schedule]
        total_time = sum(item['duration'] + ad['duration'] + durations[i] for i, item in enumerate(plan))
        elapsed = 0
        for index, clip in enumerate(plan):
            if job['_cancel'].is_set():
                break
            job.update(stage=f'Рендер {index+1} из {len(plan)}', current=index+1)
            name = 'preview.mp4' if preview else f'clip_{index+1:03d}.mp4'
            dest = Path(job['directory']) / name
            partial = dest.with_suffix('.partial.mp4')
            title_path = Path(job['directory']) / 'title.png'
            hook = schedule[index]
            hook_duration = durations[index]
            args = command(source, ad, settings, clip, partial, title_path if title_path.exists() else None, hook)
            with (Path(job['directory']) / 'ffmpeg.log').open('a', encoding='utf-8') as log:
                process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=log, text=True,
                                           encoding='utf-8', errors='replace', creationflags=CREATE_FLAGS)
                with LOCK:
                    job['_process'] = process
                    if job['_cancel'].is_set():
                        process.terminate()
                for line in process.stdout:
                    if line.startswith('out_time_us='):
                        try:
                            seconds = int(line.strip().split('=')[1]) / 1e6
                            clip_duration = clip['duration'] + ad['duration'] + hook_duration
                            job['progress'] = min(99, round(100 * (elapsed + min(seconds, clip_duration)) / total_time, 1))
                        except ValueError:
                            pass
                process.wait()
                process.stdout.close()
            with LOCK:
                job['_process'] = None
            if job['_cancel'].is_set():
                break
            if process.returncode:
                tail = (Path(job['directory']) / 'ffmpeg.log').read_text(encoding='utf-8', errors='replace')[-2000:]
                raise RuntimeError('FFmpeg не смог закончить экспорт. ' + tail)
            partial.replace(dest)
            partial = None
            clip_duration = clip['duration'] + ad['duration'] + hook_duration
            elapsed += clip_duration
            job['files'].append({'name': name, 'url': f'/output/{job["id"]}/{name}', 'duration': clip_duration,
                                 'bait': hook['name'] if hook else None})
        job.update(status='cancelled' if job['_cancel'].is_set() else 'done',
                   stage='Остановлено' if job['_cancel'].is_set() else 'Готово')
        if job['status'] == 'done':
            job['progress'] = 100
    except Exception as error:
        job.update(status='cancelled' if job['_cancel'].is_set() else 'error', stage='Остановлено' if job['_cancel'].is_set() else 'Ошибка', error=str(error))
    finally:
        if partial and partial.exists():
            partial.unlink()
        job['_process'] = None
        (Path(job['directory']) / 'result.json').write_text(json.dumps(public_job(job), ensure_ascii=False, indent=2), encoding='utf-8')

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        if len(args) > 1 and str(args[1]).startswith(('4','5')):
            super().log_message(fmt, *args)

    def allowed(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def reply(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {'error': 'Local requests only'})
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path == '/api/state':
                return self.reply(200, {'app': 'cliproom', 'token': TOKEN, 'ffmpeg': bool(binary('ffmpeg') and binary('ffprobe')),
                    'settings': CONFIG_DATA['settings'], 'banner': next((m for m in MEDIA.values() if m['path'] == CONFIG_DATA['banner_path']), None),
                    'hooks': [m for path in CONFIG_DATA.get('hook_paths', []) for m in MEDIA.values() if m['path'] == path],
                    'output_dir': CONFIG_DATA['output_dir'], 'jobs': [public_job(j) for j in JOBS.values()]})
            if path == '/api/encoders':
                return self.reply(200, {'encoders': available_encoders()})
            if path.startswith('/api/jobs/'):
                with LOCK:
                    return self.reply(200, public_job(JOBS[path.split('/')[-1]]))
            if path == '/frame':
                media = MEDIA[query['id'][0]]
                at = max(0, min(float(query.get('time', ['0'])[0]), max(0, media['duration'] - .1)))
                cache = DATA / 'frames' / f'{media["id"]}_{at:.2f}.jpg'
                with FRAME_LOCK:
                    if not cache.exists():
                        tmp = cache.with_name(cache.stem + '-' + uuid.uuid4().hex + '.jpg')
                        try:
                            res = run([binary('ffmpeg'), '-v', 'error', '-nostdin', '-ss', str(at), '-i', media['path'],
                                '-frames:v', '1', '-vf', 'scale=1280:1280:force_original_aspect_ratio=decrease,setsar=1', '-q:v', '3', '-y', str(tmp)],
                                capture_output=True, timeout=40)
                            if res.returncode or not tmp.exists():
                                raise ValueError('Не удалось получить кадр. Попробуйте другую позицию.')
                            tmp.replace(cache)
                        finally:
                            if tmp.exists():
                                tmp.unlink()
                return self.send_file(cache)
            if path.startswith('/media/'):
                return self.send_file(Path(MEDIA[path.split('/')[-1]]['path']))
            if path.startswith('/output/'):
                _,_,job_id,name = path.split('/')
                job = JOBS[job_id]
                if name not in [f['name'] for f in job['files']]:
                    raise KeyError(name)
                return self.send_file(Path(job['directory']) / name)
            if path == '/favicon.ico':
                self.send_response(204)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            assets = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
            if path in assets:
                return self.send_file(ROOT / 'static' / assets[path])
            return self.reply(404, {'error': 'Не найдено'})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except KeyError:
            self.reply(404, {'error': 'Файл или задача не найдены.'})
        except Exception as error:
            self.reply(400, {'error': str(error)})

    def do_POST(self):
        origin = self.headers.get('Origin')
        if not self.allowed() or self.headers.get('X-App-Token') != TOKEN or (origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')):
            self.close_connection = True
            return self.reply(403, {'error': 'Обновите страницу приложения.'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 2 * 1024 * 1024:
                self.close_connection = True
                return self.reply(413, {'error': 'Слишком большой запрос'})
            body = json.loads(self.rfile.read(length) or '{}')
            if self.path == '/api/pick':
                if not PICKER_LOCK.acquire(blocking=False):
                    raise ValueError('Окно выбора файла уже открыто.')
                try:
                    result = run([sys.executable, str(ROOT / 'picker.py'), body.get('kind', 'video')],
                                 capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
                    if result.returncode:
                        raise ValueError('Не удалось открыть окно выбора. Вставьте путь вручную.')
                    return self.reply(200, json.loads(result.stdout))
                finally:
                    PICKER_LOCK.release()
            if self.path == '/api/media':
                media = register(body['path'])
                if body.get('role') == 'banner':
                    with LOCK:
                        CONFIG_DATA['banner_path'] = media['path']
                        save_config()
                elif body.get('role') == 'hook':
                    with LOCK:
                        paths = CONFIG_DATA.setdefault('hook_paths', [])
                        if media['path'] not in paths:
                            paths.append(media['path'])
                        save_config()
                return self.reply(200, media)
            if self.path == '/api/hook/clear':
                with LOCK:
                    media_id = body.get('id')
                    if media_id:
                        media = MEDIA.get(media_id)
                        if media:
                            CONFIG_DATA['hook_paths'] = [p for p in CONFIG_DATA.get('hook_paths', []) if p != media['path']]
                    else:
                        CONFIG_DATA['hook_paths'] = []
                    save_config()
                return self.reply(200, {'ok': True})
            if self.path == '/api/settings':
                with LOCK:
                    CONFIG_DATA['settings'] = validate_settings(body['settings'])
                    save_config()
                return self.reply(200, {'ok': True})
            if self.path == '/api/output-dir':
                folder = Path(body['path']).expanduser().resolve()
                if not folder.is_dir():
                    raise ValueError('Выберите существующую папку.')
                with LOCK:
                    CONFIG_DATA['output_dir'] = str(folder)
                    save_config()
                return self.reply(200, {'path': str(folder)})
            if self.path == '/api/render':
                source, ad = MEDIA[body['source']], MEDIA[body['banner']]
                hooks = [MEDIA[key] for key in body.get('hooks', []) if key in MEDIA]
                settings = validate_settings(body['settings'])
                # Pool items are prepared clips and are intentionally used in full.
                settings['hook_start'] = 0
                settings['hook_end'] = 0
                if settings['export_encoder'] not in {item['id'] for item in available_encoders()}:
                    raise ValueError('Выбранный GPU-кодировщик сейчас недоступен. Выберите CPU или обновите драйвер видеокарты.')
                preview = bool(body.get('preview', False))
                plan = clip_plan(source, settings, preview)
                for hook in hooks:
                    hook_range(hook, settings)
                schedule = bait_schedule(hooks, len(plan))
                title_png = None
                if settings['title_top'].strip() or settings['title_bottom'].strip():
                    title_png = base64.b64decode(body.get('title_image', ''), validate=True)
                    if (len(title_png) < 24 or title_png[:8] != b'\x89PNG\r\n\x1a\n'
                            or struct.unpack('>II', title_png[16:24]) != (720, 1280)):
                        raise ValueError('Не удалось подготовить текст. Обновите страницу и повторите экспорт.')
                with LOCK:
                    if any(j['status'] == 'running' for j in JOBS.values()):
                        raise ValueError('Дождитесь текущего экспорта или остановите его.')
                    job_id = uuid.uuid4().hex[:12]
                    base = DATA / 'previews' if preview else Path(CONFIG_DATA['output_dir'])
                    directory = base / (time.strftime('%Y-%m-%d_%H-%M-%S_') + job_id)
                    directory.mkdir(parents=True)
                    if title_png:
                        (directory / 'title.png').write_bytes(title_png)
                    if shutil.disk_usage(directory).free < 300 * 1024 * 1024:
                        raise ValueError('Недостаточно места: освободите хотя бы 300 МБ.')
                    job = {'id': job_id, 'status': 'running', 'progress': 0, 'stage': 'Подготовка', 'files': [],
                           'directory': str(directory), 'preview': preview, 'total': len(plan),
                           'source_name': source['name'], 'encoder': settings['export_encoder'],
                           '_cancel': threading.Event(), '_process': None}
                    JOBS[job_id] = job
                    CONFIG_DATA['settings'] = settings
                    save_config()
                    schedule_names = [hook['name'] if hook else None for hook in schedule]
                    (directory / 'project.json').write_text(json.dumps({'source': source, 'banner': ad, 'hooks': hooks,
                        'bait_schedule': schedule_names, 'settings': settings}, ensure_ascii=False, indent=2), encoding='utf-8')
                    threading.Thread(target=render_job, args=(job,source,ad,hooks,settings,preview,plan,schedule), daemon=True).start()
                return self.reply(200, public_job(job))
            if self.path == '/api/cancel':
                with LOCK:
                    job = JOBS[body['id']]
                    if job['status'] == 'running':
                        job['_cancel'].set()
                        if job['_process'] and job['_process'].poll() is None:
                            job['_process'].terminate()
                return self.reply(200, {'ok': True})
            if self.path == '/api/open-folder':
                folder = Path(JOBS[body['id']]['directory']) if body.get('id') else Path(CONFIG_DATA['output_dir'])
                folder.mkdir(parents=True, exist_ok=True)
                if os.name == 'nt':
                    os.startfile(str(folder))
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', str(folder)])
                else:
                    subprocess.Popen(['xdg-open', str(folder)])
                return self.reply(200, {'ok': True})
            self.reply(404, {'error': 'Не найдено'})
        except (KeyError, ValueError, TypeError, OSError, subprocess.TimeoutExpired) as error:
            self.reply(400, {'error': str(error)})

    def send_file(self, path):
        size = path.stat().st_size
        start, end = 0, size - 1
        partial = self.headers.get('Range')
        if partial:
            try:
                unit, span = partial.split('=')
                first, last = span.split('-')
                if unit != 'bytes':
                    raise ValueError()
                start = int(first) if first else max(0, size - int(last))
                end = min(size - 1, int(last)) if first and last else size - 1
                if not 0 <= start <= end < size:
                    raise ValueError()
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(end-start+1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        with path.open('rb') as stream:
            stream.seek(start)
            remaining = end-start+1
            while remaining > 0:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--no-setup', action='store_true', help='Start UI while an external FFmpeg installer is running')
    args = parser.parse_args()
    address = f'http://127.0.0.1:{args.port}'
    try:
        with urllib.request.urlopen(address + '/api/state', timeout=1) as response:
            existing = json.load(response)
        if existing.get('app') == 'cliproom':
            print(f'Cliproom is already running at {address}', flush=True)
            if args.open:
                webbrowser.open(address)
            return
    except (OSError, ValueError):
        pass
    if not args.no_setup and (not binary('ffmpeg') or not binary('ffprobe')):
        from setup_ffmpeg import install
        install()
    if CONFIG_DATA.get('banner_path'):
        try:
            register(CONFIG_DATA['banner_path'])
        except Exception:
            pass
    valid_hook_paths = []
    for hook_path in CONFIG_DATA.get('hook_paths', []):
        try:
            register(hook_path)
            valid_hook_paths.append(hook_path)
        except Exception:
            pass
    CONFIG_DATA['hook_paths'] = valid_hook_paths
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        print(f'Port {args.port} is busy. Use: python app.py --port 8766 --open')
        raise SystemExit(1)
    print(f'Cliproom is running at {address} | Ctrl+C to stop', flush=True)
    if args.open:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        with LOCK:
            for job in JOBS.values():
                job['_cancel'].set()
                if job['_process'] and job['_process'].poll() is None:
                    job['_process'].terminate()
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
