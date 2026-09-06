"""FFmpeg rendering engine. Source time never advances while the ad is playing."""
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
FPS = 30

def binary(name):
    local = ROOT / '.tools' / (name + ('.exe' if os.name == 'nt' else ''))
    return str(local) if local.exists() else shutil.which(name)

def run(args, **kwargs):
    return subprocess.run(args, creationflags=CREATE_FLAGS, **kwargs)

def probe(path):
    if not binary('ffprobe'):
        raise ValueError('FFmpeg не установлен. Запустите python setup_ffmpeg.py.')
    result = run([binary('ffprobe'), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                 capture_output=True, encoding='utf-8', errors='replace', timeout=40)
    if result.returncode:
        raise ValueError('Не удалось прочитать видео. Проверьте файл. ' + result.stderr[-400:])
    info = json.loads(result.stdout)
    video = next((s for s in info['streams'] if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    if not video:
        raise ValueError('В файле нет видеодорожки.')
    duration = float(video.get('duration') or info.get('format', {}).get('duration') or 0)
    if not math.isfinite(duration) or duration < 2 / FPS:
        raise ValueError('Видео слишком короткое или его длительность неизвестна.')
    width, height = video['width'], video['height']
    rotation = next((int(s.get('rotation', 0)) for s in video.get('side_data_list', []) if 'rotation' in s), 0)
    rotation = int(video.get('tags', {}).get('rotate', rotation))
    if abs(rotation) % 180 == 90:
        width, height = height, width
    return {'path': str(path), 'name': Path(path).name, 'duration': duration, 'width': width, 'height': height,
            'audio': any(s['codec_type'] == 'audio' for s in info['streams']), 'size': Path(path).stat().st_size}

def validate_settings(raw):
    result = {'resolution': str(raw.get('resolution', '720')), 'preset': raw.get('preset', 'veryfast'),
              'face_share': float(raw.get('face_share', 35)), 'face_mode': raw.get('face_mode', 'crop'),
              'content_mode': raw.get('content_mode', 'fit'), 'ad_mode': raw.get('ad_mode', 'fit')}
    if result['resolution'] not in ('720', '1080') or result['preset'] not in ('ultrafast', 'veryfast', 'fast'):
        raise ValueError('Неизвестный формат экспорта.')
    if not math.isfinite(result['face_share']) or not 20 <= result['face_share'] <= 60:
        raise ValueError('Высота вебки должна быть от 20 до 60%.')
    for key in ('face_mode', 'content_mode', 'ad_mode'):
        if result[key] not in ('crop', 'fit'):
            raise ValueError('Неизвестный режим заполнения.')
    for key, default in [('ad_x', 50), ('ad_y', 72)]:
        result[key] = float(raw.get(key, default))
        if not math.isfinite(result[key]) or not 0 <= result[key] <= 100:
            raise ValueError('Положение баннера должно быть от 0 до 100%.')
    for key in ('title_top', 'title_bottom'):
        value = raw.get(key, '')
        if not isinstance(value, str) or len(value) > 80 or any(ord(c) < 32 for c in value):
            raise ValueError('Строка заголовка должна содержать не более 80 символов без переносов.')
        result[key] = value
    result['title_size'] = float(raw.get('title_size', 72))
    if not math.isfinite(result['title_size']) or not 36 <= result['title_size'] <= 110:
        raise ValueError('Размер текста должен быть от 36 до 110.')
    for key, default in [('face', [0, 0, .3, .3]), ('content', [0, 0, 1, 1])]:
        rect = raw.get(key, default)
        if not isinstance(rect, list) or len(rect) != 4:
            raise ValueError('Некорректная область кадра.')
        x, y, w, h = map(float, rect)
        if not all(math.isfinite(n) for n in (x,y,w,h)) or min(x,y) < 0 or min(w,h) < .01 or x+w > 1.000001 or y+h > 1.000001:
            raise ValueError('Область должна находиться внутри кадра и занимать не менее 1%.')
        result[key] = [x, y, w, h]
    return result

def segments(duration):
    # Work on the output frame grid; keep even a one-frame remainder.
    frames = max(2, math.ceil(duration * FPS - 1e-6))
    return [(start / FPS, min(60 * FPS, frames - start) / FPS) for start in range(0, frames, 60 * FPS)]

def fit_filter(width, height, mode):
    if mode == 'crop':
        return f'scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2,crop={width}:{height},setsar=1'
    return f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1'

def crop_filter(rect, source):
    sw, sh = source['width'], source['height']
    x,y,w,h = rect
    # Even coordinates match yuv420p. Clamp after rounding so crop cannot leave the frame.
    cw = min(sw // 2 * 2, max(2, int(w * sw) // 2 * 2))
    ch = min(sh // 2 * 2, max(2, int(h * sh) // 2 * 2))
    cx = min(sw - cw, int(x * sw) // 2 * 2)
    cy = min(sh - ch, int(y * sh) // 2 * 2)
    return f'crop={cw}:{ch}:{cx}:{cy}'

def command(source, ad, settings, start, duration, output, title_path=None):
    width = int(settings['resolution'])
    height = width * 16 // 9
    face_height = round(height * settings['face_share'] / 100 / 2) * 2
    half = math.floor(duration * FPS / 2) / FPS
    # A one-frame tail has no room for two halves: place its ad before that frame.
    parts = [(source, start, half, 'source'), (ad, 0, ad['duration'], 'ad'),
             (source, start + half, duration - half, 'source')]
    parts = [p for p in parts if p[2] > 1e-8]
    args = [binary('ffmpeg'), '-hide_banner', '-nostdin', '-y', '-filter_complex_threads', '2']
    for media, offset, length, kind in parts:
        args += ['-ss', f'{offset:.9f}', '-t', f'{length:.9f}', '-i', media['path']]
    frozen_index = len(parts)
    freeze_at = start + max(0, half - 1 / FPS)
    args += ['-ss', f'{freeze_at:.9f}', '-i', source['path']]
    if title_path:
        args += ['-loop', '1', '-framerate', str(FPS), '-i', str(title_path)]
    filters = []
    def layout(prefix, tag):
        filters.extend([
            prefix + f',split=2[f{tag}][c{tag}]',
            f'[f{tag}]{crop_filter(settings["face"], source)},{fit_filter(width, face_height, settings["face_mode"])}[ft{tag}]',
            f'[c{tag}]{crop_filter(settings["content"], source)},{fit_filter(width, height-face_height, settings["content_mode"])}[ct{tag}]',
            f'[ft{tag}][ct{tag}]vstack=inputs=2,format=yuv420p[base{tag}]'])
    layout(f'[{frozen_index}:v:0]trim=end_frame=1,setpts=PTS-STARTPTS,fps={FPS},tpad=stop_mode=clone:stop_duration={ad["duration"]},trim=duration={ad["duration"]}', 'bg')
    if title_path:
        filters += [f'[{frozen_index+1}:v:0]scale={width}:{height},format=rgba,split={len(parts)}' + ''.join(f'[title{i}]' for i in range(len(parts)))]
    for i, (media, offset, length, kind) in enumerate(parts):
        prefix = f'[{i}:v:0]setpts=PTS-STARTPTS,fps={FPS},tpad=stop_mode=clone:stop_duration=0.1,trim=duration={length:.9f},setsar=1'
        if kind == 'source':
            layout(prefix, str(i))
        else:
            bw, bh = width // 20 * 18, height // 20 * 6
            banner_filter = (fit_filter(bw, bh, 'crop') if settings['ad_mode'] == 'crop' else
                             f'scale={bw}:{bh}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1')
            filters += [prefix + ',' + banner_filter + f'[banner{i}]',
                        f'[basebg][banner{i}]overlay=x=\'max(0,min(W-w,W*{settings.get("ad_x", 50)/100}-w/2))\':'
                        f'y=\'max(0,min(H-h,H*{settings.get("ad_y", 72)/100}-h/2))\':shortest=1[base{i}]']
        filters += [f'[base{i}][title{i}]overlay=shortest=1,format=yuv420p[v{i}]' if title_path else f'[base{i}]null[v{i}]']
        audio = f'[{i}:a:0]aresample=48000:async=1:first_pts=0,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS,apad' if media['audio'] else 'anullsrc=r=48000:cl=stereo'
        filters += [audio + f',atrim=duration={length:.9f},asetpts=PTS-STARTPTS[a{i}]']
    filters += [''.join(f'[v{i}][a{i}]' for i in range(len(parts))) + f'concat=n={len(parts)}:v=1:a=1[outv][outa]']
    args += ['-filter_complex', ';'.join(filters), '-map', '[outv]', '-map', '[outa]',
             '-c:v', 'libx264', '-preset', settings['preset'], '-crf', '23', '-pix_fmt', 'yuv420p', '-r', str(FPS),
             '-threads', str(max(1, min(8, (os.cpu_count() or 4) - 1))), '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
             '-movflags', '+faststart', '-map_metadata', '-1', '-progress', 'pipe:1', '-nostats', str(output)]
    return args
