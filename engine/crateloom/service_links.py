"""Public track references from provider oEmbed endpoints. Never fetch streams."""
import json
import re
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from . import download_http

YOUTUBE = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'}
SPOTIFY = {'open.spotify.com', 'spotify.link'}
SOUNDCLOUD = {'soundcloud.com', 'www.soundcloud.com', 'm.soundcloud.com', 'on.soundcloud.com'}
ENDPOINTS = {
    'YouTube': 'https://www.youtube.com/oembed',
    'YouTube Music': 'https://www.youtube.com/oembed',
    'Spotify': 'https://open.spotify.com/oembed',
    'SoundCloud': 'https://soundcloud.com/oembed',
}
# Share-menu and campaign parameters; they never change which mix or playlist a link opens.
TRACKING = {'si', 'pp', 'feature', 'ref', 'fbclid', 'igsh', 'igshid', 't'}
GUIDANCE = {
    'YouTube': 'Use a direct audio file from the creator to import this track.',
    'YouTube Music': 'Use a direct audio file from the creator to import this track.',
    'Spotify': 'Use an independently obtained audio file to import this track.',
    'SoundCloud': 'If the creator offers Download file, use it on SoundCloud, then add the file here.',
}


def strip_tracking(url):
    """Drop share and campaign parameters; v, list and everything else stay as written."""
    if not isinstance(url, str):
        return url
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return url
    if not parsed.query:
        return url.strip()
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    kept = [(name, value) for name, value in pairs if name.lower() not in TRACKING and not name.lower().startswith('utm_')]
    if len(kept) == len(pairs):
        return url.strip()
    return urlunsplit(parsed._replace(query=urlencode(kept)))


def identify(url):
    parsed = urlsplit(url)
    host = parsed.hostname
    parts = parsed.path.strip('/').split('/')
    if host in YOUTUBE:
        provider = 'YouTube Music' if host == 'music.youtube.com' else 'YouTube'
        vid = (parts[0] if host == 'youtu.be' and len(parts) == 1 else
               parse_qs(parsed.query).get('v', [''])[0] if parts == ['watch'] else
               parts[1] if len(parts) == 2 and parts[0] in ('shorts', 'embed', 'live') else '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid):
            raise ValueError(f'Paste a single {provider} video or song link.')
        return provider, f'https://{"music.youtube.com" if provider == "YouTube Music" else "www.youtube.com"}/watch?v={vid}', 'https://www.youtube.com/watch?v='+vid
    if host in SPOTIFY:
        if host == 'spotify.link' and len(parts) == 1 and re.fullmatch(r'[A-Za-z0-9]+', parts[0]):
            return 'Spotify', 'https://spotify.link/'+parts[0], 'https://spotify.link/'+parts[0]
        if parts and re.fullmatch(r'intl-[a-zA-Z-]+', parts[0]):
            parts = parts[1:]
        if len(parts) != 2 or parts[0] not in ('track','album','playlist') or not re.fullmatch(r'[A-Za-z0-9]{22}', parts[1]):
            raise ValueError('Paste a Spotify track, album or playlist link.')
        canonical = 'https://open.spotify.com/'+'/'.join(parts)
        return 'Spotify', canonical, canonical
    if host in SOUNDCLOUD:
        if host == 'on.soundcloud.com' and len(parts) in (1,2) and all(re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in parts):
            canonical = 'https://on.soundcloud.com/'+'/'.join(parts)
        elif len(parts) in (2,3) and all(re.fullmatch(r'[a-zA-Z0-9_-]+', p) for p in parts) and (len(parts)==2 or parts[1]=='sets'):
            canonical = 'https://soundcloud.com/'+'/'.join(parts)
        else:
            raise ValueError('Paste a public SoundCloud track or set link.')
        return 'SoundCloud', canonical, canonical
    if any(host == domain or host.endswith('.'+domain) for domain in ('youtube.com','spotify.com','soundcloud.com','soundcloud.app.goo.gl')):
        raise ValueError('Paste the public track link from the service’s Share menu.')
    return None


def resolve(reference):
    provider, source, lookup = reference
    endpoint = ENDPOINTS[provider]+'?'+urlencode({'url':lookup,'format':'json'})
    try:
        with download_http.open_url(endpoint) as response:
            payload = response.read(256*1024+1)
            if len(payload)>256*1024:
                raise ValueError('Reference response is too large')
        metadata = json.loads(payload)
        title = clean(metadata.get('title'))
        if not title:
            raise ValueError('No public title')
    except (OSError, ValueError, AttributeError) as error:
        raise ValueError(f'{provider} could not load this reference. Check that the link is public and try again.') from error
    creator = clean(metadata.get('author_name'))
    # Uploader/channel is attribution, not necessarily the recording artist.
    if provider == 'SoundCloud' and creator and title.endswith(' by '+creator):
        title = title[:-len(' by '+creator)] or title
    return {'kind':'reference','provider':provider,'source_url':source,'values':{'title':title,'artist':''},
            'creator':creator,'version':'','can_acquire':False,'guidance':GUIDANCE[provider]}


def clean(value):
    return ''.join(c for c in value if c.isprintable()).strip()[:300] if isinstance(value,str) else ''
