#!/usr/bin/env python3
"""Install the pinned, checksum-verified YouTube helper on native storage."""
import hashlib
import os
from pathlib import Path
import platform
import urllib.request

VERSION = '2026.10.26'
SHA256 = '0f922c15ccc0b83eefa1c8bc9104e434318c4bf16e408e1ee93b7745a6efe0aa'


def install():
    if platform.system() != 'Darwin':
        raise SystemExit('This helper installer supports macOS.')
    folder = Path.home()/'Library/Caches/CrateLoom/tools'/VERSION
    folder.mkdir(parents=True, exist_ok=True)
    target = folder/'yt-dlp'
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == SHA256:
        return target
    temporary = folder/f'yt-dlp-{os.getpid()}.download'
    try:
        url = f'https://github.com/yt-dlp/yt-dlp/releases/download/{VERSION}/yt-dlp_macos'
        with urllib.request.urlopen(url, timeout=30) as response, temporary.open('xb') as output:
            size = 0
            while chunk := response.read(1024*1024):
                size += len(chunk)
                if size > 64*1024*1024:
                    raise ValueError('The YouTube helper exceeded its expected size.')
                output.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != SHA256:
            raise ValueError('The YouTube helper checksum did not match the pinned release.')
        temporary.chmod(0o755)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


if __name__ == '__main__':
    print(install())
