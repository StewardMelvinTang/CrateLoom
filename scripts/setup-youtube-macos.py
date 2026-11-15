#!/usr/bin/env python3
"""Install the pinned, checksum-verified YouTube helper on native storage."""
import hashlib
import os
from pathlib import Path
import platform
import sys
import urllib.request

VERSION = '2026.10.26'
SHA256 = '0f922c15ccc0b83eefa1c8bc9104e434318c4bf16e408e1ee93b7745a6efe0aa'
# The unpacked ("onedir") build of the same release starts in a fraction of the
# onefile build's time. The engine extracts it on first use and falls back to the
# onefile helper whenever it is missing or unusable.
ONEDIR = 'yt-dlp_macos.zip'
ONEDIR_SHA256 = '20d98fa608998eff87d1d35a6e9fb65e4b874639a6cc89addc19da3becde3ef1'


def fetch(name, target, digest):
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
        return target
    temporary = target.with_name(f'{target.name}-{os.getpid()}.download')
    try:
        url = f'https://github.com/yt-dlp/yt-dlp/releases/download/{VERSION}/{name}'
        with urllib.request.urlopen(url, timeout=30) as response, temporary.open('xb') as output:
            size = 0
            while chunk := response.read(1024*1024):
                size += len(chunk)
                if size > 64*1024*1024:
                    raise ValueError('The YouTube helper exceeded its expected size.')
                output.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
            raise ValueError('The YouTube helper checksum did not match the pinned release.')
        temporary.chmod(0o755 if target.suffix != '.zip' else 0o644)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def install():
    if platform.system() != 'Darwin':
        raise SystemExit('This helper installer supports macOS.')
    folder = Path.home()/'Library/Caches/CrateLoom/tools'/VERSION
    folder.mkdir(parents=True, exist_ok=True)
    target = fetch('yt-dlp_macos', folder/'yt-dlp', SHA256)
    fetch(ONEDIR, folder/ONEDIR, ONEDIR_SHA256)
    return target


if __name__ == '__main__':
    helper = install()
    # The build script reads the first line; --onedir prints the archive path instead.
    print(helper.with_name(ONEDIR) if '--onedir' in sys.argv[1:] else helper)
