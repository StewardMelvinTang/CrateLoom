"""Smoke-test a frozen sidecar in a temporary library, without PYTHONPATH."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

binary = Path(sys.argv[1]).resolve(strict=True)
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    wav = root / 'sidecar test.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=2', '-y', str(wav)], check=True)
    # ffmpeg/ffprobe are external dependencies; PATH is retained for those programs.
    env = {'HOME': temp, 'PATH': os.environ['PATH'], 'CRATELOOM_DATA': str(root / 'data')}
    env.pop('PYTHONPATH', None)
    proc = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)
    def call(method, params):
        proc.stdin.write(json.dumps({'protocol_version': 1, 'request_id': method,
                                     'method': method, 'params': params}) + '\n')
        proc.stdin.flush()
        response = json.loads(proc.stdout.readline())
        if response.get('error'):
            raise RuntimeError(response['error'])
        if response.get('request_id') != method or response.get('protocol_version') != 1:
            raise RuntimeError('Unexpected sidecar response')
        return response['result']
    try:
        handshake = call('handshake', {})
        track = call('library.import', {'path': str(wav)})
        result = call('analysis.run', {'id': track['id']})
        assert len(result['analysis']['peaks']) > 0
        print(f"Sidecar {handshake['version']}: imported {track['duration']:.2f}s audio and computed {len(result['analysis']['peaks'])} peaks")
    finally:
        proc.stdin.close()
        proc.wait(timeout=15)
        proc.stdout.close()
        stderr = proc.stderr.read()
        proc.stderr.close()
        if proc.returncode:
            raise RuntimeError(f'Sidecar exited {proc.returncode}: {stderr}')
