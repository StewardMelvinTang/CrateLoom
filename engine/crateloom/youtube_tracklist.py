"""Read published YouTube metadata when the extractor cannot supply it."""
import json
import re
import subprocess
from urllib.parse import parse_qs, urlsplit

from . import download_http, set_scan_jobs


def embedded_json(html, name):
    for pattern in (re.escape(name)+r'\s*=\s*', r'"'+re.escape(name)+r'"\s*:\s*'):
        for match in re.finditer(pattern, html):
            try:
                value, _ = json.JSONDecoder().raw_decode(html[match.end():])
                if isinstance(value, dict):
                    return value
            except ValueError:
                continue
    return {}


def text(value):
    if not isinstance(value, dict):
        return ''
    return value.get('simpleText') or ''.join(run.get('text','') for run in value.get('runs',[]) if isinstance(run,dict))


def descriptions(value):
    rows = []
    if isinstance(value,dict):
        for key,child in value.items():
            if key=='attributedDescription' and isinstance(child,dict) and isinstance(child.get('content'),str):
                rows.append(child['content'])
            elif key=='videoSecondaryInfoRenderer' and isinstance(child,dict):
                rows.append(text(child.get('description')))
            rows.extend(descriptions(child))
    elif isinstance(value,list):
        for child in value:
            rows.extend(descriptions(child))
    return rows


def chapter_rows(value):
    rows = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ('chapterRenderer','macroMarkersListItemRenderer') and isinstance(child,dict):
                at = child.get('timeRangeStartMillis')
                if type(at) in (int,float):
                    at /= 1000
                else:
                    stamp = text(child.get('timeDescription'))
                    at = sum(int(part)*60**i for i,part in enumerate(reversed(stamp.split(':')))) if re.fullmatch(r'(?:\d{1,3}:)?\d{1,3}:\d{2}',stamp) else None
                title = text(child.get('title'))
                if at is not None and at >= 0 and title:
                    rows.append({'start_time':at,'title':title})
            rows.extend(chapter_rows(child))
    elif isinstance(value,list):
        for child in value:
            rows.extend(chapter_rows(child))
    return rows


def from_page(html, video_id):
    player = embedded_json(html,'ytInitialPlayerResponse')
    details = player.get('videoDetails') or {}
    if details.get('videoId') != video_id:
        raise ValueError('YouTube did not return metadata for this video.')
    initial = embedded_json(html,'ytInitialData')
    chapters = chapter_rows(initial)
    duration = details.get('lengthSeconds')
    description = max([details.get('shortDescription') or '',*descriptions(initial)],key=len)
    return {'id':video_id,'title':details.get('title',''),'description':description,
            'channel':details.get('author',''),'duration':int(duration) if isinstance(duration,str) and duration.isdigit() else None,
            'is_live':bool(details.get('isLiveContent') and player.get('playabilityStatus',{}).get('liveStreamability')),
            'chapters':list({row['start_time']:row for row in sorted(chapters,key=lambda row:row['start_time'])}.values())}


def read_comments(api,watch):
    from . import youtube_audio
    binary = youtube_audio.executable()
    if not binary:
        return []
    set_scan_jobs.progress('Checking published comment tracklists')
    try:
        response = api.import_process([
            binary,'--ignore-config','--no-plugin-dirs','--no-remote-components','--proxy','',
            '--no-playlist','--no-warnings','--skip-download','--ignore-no-formats-error',
            '--socket-timeout','6','--retries','0','--extractor-retries','0',
            '--get-comments','--extractor-args','youtube:comment_sort=top;max_comments=30,30,0,0;skip=hls,dash;player_skip=js',
            *youtube_audio.runtime_args(),'--print','%(.{id,comments})j','--',watch,
        ],22,process_group=True)
        data = json.loads(response.stdout)
        expected = parse_qs(urlsplit(watch).query)['v'][0]
        if data.get('id') != expected:
            return []
        return [comment['text'][:100000] for comment in (data.get('comments') or [])[:30]
                if isinstance(comment,dict) and isinstance(comment.get('text'),str)]
    except (OSError,ValueError,subprocess.SubprocessError):
        return []


def read(watch):
    video_id = parse_qs(urlsplit(watch).query).get('v',[''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):
        raise ValueError('Choose a single YouTube video.')
    set_scan_jobs.check()
    with download_http.open_url(watch,headers={'Accept-Language':'en-US,en;q=0.9'}) as response:
        data = response.read(6*1024*1024+1)
    set_scan_jobs.check()
    if len(data)>6*1024*1024:
        raise ValueError('YouTube returned too much page data.')
    return from_page(data.decode('utf-8',errors='replace'),video_id)
