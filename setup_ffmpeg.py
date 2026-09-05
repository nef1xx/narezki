"""Install portable FFmpeg from a provider linked by ffmpeg.org; verify SHA-256."""
import argparse
import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def install(archive_path=None):
    target = ROOT / '.tools'
    target.mkdir(exist_ok=True)
    if all((target / name).exists() for name in ('ffmpeg.exe', 'ffprobe.exe')):
        print('Portable FFmpeg is ready.')
        return
    url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    archive = Path(archive_path) if archive_path else target / 'ffmpeg.zip'
    print('Preparing portable FFmpeg (first launch only)...', flush=True)
    with urllib.request.urlopen(url + '.sha256', timeout=60) as response:
        expected = response.read().decode().split()[0].lower()
    if not archive_path:
        urllib.request.urlretrieve(url, archive)
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise RuntimeError('FFmpeg checksum mismatch. Please run setup again.')
    with zipfile.ZipFile(archive) as bundle:
        for name in ('ffmpeg.exe', 'ffprobe.exe'):
            member = next(n for n in bundle.namelist() if n.endswith('/bin/' + name))
            with bundle.open(member) as source, (target / name).open('wb') as dest:
                shutil.copyfileobj(source, dest)
        licenses = [n for n in bundle.namelist() if n.endswith('LICENSE') or n.endswith('LICENSE.txt')]
        if licenses:
            (target / 'FFmpeg-LICENSE.txt').write_bytes(bundle.read(licenses[0]))
    archive.unlink()
    print('FFmpeg installed and checksum verified.', flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', help='Install an already downloaded Gyan release archive')
    install(parser.parse_args().archive)
