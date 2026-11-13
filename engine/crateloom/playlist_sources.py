"""Read ordered public playlist entries. Audio acquisition remains separate."""
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

from . import download_http, service_links, youtube_audio, set_scan_jobs
from .metadata_matching import clean_name, search_identity

MAX_ENTRIES = 1000


def identify(url):
    host = urllib.parse.urlsplit(url).hostname
    if host in ('spotify.link', 'on.soundcloud.com'):
        with download_http.open_url(url, method='HEAD') as response:
            url = download_http.public_url(response.url)
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    if host in service_links.YOUTUBE:
        playlist = urllib.parse.parse_qs(parsed.query).get('list', [''])[0]
        if re.fullmatch(r'[A-Za-z0-9_-]{8,100}', playlist) and (parsed.path == '/playlist' or not playlist.startswith('RD')):
            provider = 'YouTube Music' if host == 'music.youtube.com' else 'YouTube'
            host = 'music.youtube.com' if provider == 'YouTube Music' else 'www.youtube.com'
            return provider, f'https://{host}/playlist?list={playlist}'
    if host in service_links.SPOTIFY | service_links.SOUNDCLOUD:
        provider, canonical, _ = service_links.identify(url)
        path = urllib.parse.urlsplit(canonical).path
        if path.startswith('/playlist/') or '/sets/' in path:
            return provider, canonical
    return None


def entry(title, artist, provider, url=None, duration=None, artwork=None):
    title = service_links.clean(title)
    return {'at': None, 'title': title or 'Unavailable track', 'artist': service_links.clean(artist),
            'evidence': [provider], 'listed_links': [url] if url and provider != 'Spotify' else [],
            'links': [{'label': provider, 'url': url}] if url else [], 'duration': duration, 'artwork_url': artwork,
            'credit_confirmed': True, 'unknown': not title or title.casefold() in ('[private video]', '[deleted video]', 'private video', 'deleted video')}


def spotify_api(url, token):
    set_scan_jobs.check()
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})
    try:
        with urllib.request.build_opener(download_http.NoRedirect()).open(req, timeout=20) as response:
            return json.loads(response.read(4 * 1024 * 1024))
    except (OSError, ValueError) as error:
        raise ValueError('Spotify authorization failed. Check playlist access or clear the expired token.') from error


def spotify(url, token=None):
    playlist_id = url.rsplit('/', 1)[-1]
    token = token or os.environ.get('CRATELOOM_SPOTIFY_TOKEN', '').strip()
    if token:
        endpoint = 'https://api.spotify.com/v1/playlists/' + playlist_id
        metadata = spotify_api(endpoint, token)
        tracks = []
        for offset in range(0, MAX_ENTRIES + 50, 50):
            set_scan_jobs.check()
            page = spotify_api(f'{endpoint}/items?limit=50&offset={offset}', token)
            for row in page['items']:
                track = row.get('item') or row.get('track') or {}
                is_track = track.get('type') == 'track'
                tracks.append(entry(track.get('name') if is_track else None,
                                    ', '.join(a['name'] for a in track.get('artists', [])), 'Spotify',
                                    track.get('external_urls', {}).get('spotify'),
                                    track['duration_ms'] / 1000 if track.get('duration_ms') else None,
                                    next((i.get('url') for i in track.get('album', {}).get('images', []) if i.get('url')), None)))
            remember(metadata['name'],tracks)
            set_scan_jobs.progress('Reading playlist entries',completed=len(tracks),total=page.get('total'),unit='entries')
            if not page.get('next'):
                return metadata['name'], tracks, None
        raise ValueError(f'This playlist exceeds {MAX_ENTRIES} tracks.')
    with download_http.open_url('https://open.spotify.com/embed/playlist/' + playlist_id) as response:
        html = response.read(4 * 1024 * 1024 + 1)
    if len(html) > 4 * 1024 * 1024:
        raise ValueError('Spotify returned too much playlist data.')
    match = re.search(rb'<script\b[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    try:
        data = json.loads(match[1])['props']['pageProps']['state']['data']['entity']
        if data['uri'] != 'spotify:playlist:' + playlist_id:
            raise ValueError()
        rows = data['trackList']
        name = data['name']
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError('Spotify could not share this playlist. Check that its link opens publicly.') from error
    tracks = []
    for row in rows:
        set_scan_jobs.check()
        uri = row.get('uri', '')
        track_id = re.fullmatch(r'spotify:track:([A-Za-z0-9]{22})', uri)
        tracks.append(entry(row.get('title') if track_id else None, row.get('subtitle'), 'Spotify',
                            'https://open.spotify.com/track/' + track_id[1] if track_id else None,
                            row['duration'] / 1000 if row.get('duration') else None,
                            row.get('imageUri') or next((i.get('url') for i in row.get('albumCoverArt', {}).get('sources', []) if i.get('url')), None)))
        remember(name,tracks)
        set_scan_jobs.progress('Reading playlist entries',completed=len(tracks),total=len(rows),unit='entries')
    return name, tracks, 'Spotify supplied its public track list. Use Spotify authorization for guaranteed pagination.'


def extractor(api, provider, url):
    set_scan_jobs.progress('Reading '+provider+' playlist')
    binary = youtube_audio.executable()
    if not binary:
        raise ValueError('Install yt-dlp to read this playlist.')
    try:
        result = youtube_audio.run_helper(api, binary, [
            '--ignore-config', '--no-plugin-dirs', '--no-remote-components',
            '--proxy', '', '--flat-playlist', '--skip-download', '--yes-playlist',
            '--socket-timeout', '10', '--retries', '1', '--playlist-end', str(MAX_ENTRIES + 1),
            '--dump-single-json', '--', url,
        ], 90)
        data = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ValueError(f'{provider} could not read this playlist. Check that the link is public.') from error
    tracks = []
    for row in data.get('entries') or []:
        set_scan_jobs.check()
        if not row:
            tracks.append(entry(None, None, provider))
            continue
        page = None
        if provider in youtube_audio.NAMES and re.fullmatch(r'[\w-]{11}', row.get('id') or '', re.ASCII):
            host = 'music.youtube.com' if provider == 'YouTube Music' else 'www.youtube.com'
            page = f"https://{host}/watch?v={row['id']}"
        elif provider == 'SoundCloud':
            try:
                reference = service_links.identify(download_http.public_url(row.get('webpage_url')))
                if reference and reference[0] == provider:
                    page = reference[1]
            except ValueError:
                pass
        title = clean_name(service_links.clean(row.get('title')))
        artist = service_links.clean(row.get('artist') or row.get('channel') or row.get('uploader'))
        artist = re.sub(r'\s+-\s+Topic$', '', artist, flags=re.I)
        identity = search_identity(title)
        if identity['artist']:
            artist = identity['artist']
            title = re.split(r'\s+[-–—]\s+', title, maxsplit=1)[1]
        tracks.append(entry(title, artist, provider, page, row.get('duration'), row.get('thumbnail')))
        remember(service_links.clean(data.get('title')) or provider+' playlist',tracks)
        set_scan_jobs.progress('Reading playlist entries',completed=len(tracks),total=len(data.get('entries') or []),unit='entries')
    return service_links.clean(data.get('title')) or f'{provider} playlist', tracks, None


def remember(name, tracks):
    control = set_scan_jobs.current()
    if control:
        control.playlist_partial=(name,list(tracks))


def read(api, reference, token=None):
    provider, url = reference
    title, tracks, warning = spotify(url, token) if provider == 'Spotify' else extractor(api, provider, url)
    if not tracks:
        raise ValueError('This playlist has no accessible tracks.')
    if len(tracks) > MAX_ENTRIES:
        raise ValueError(f'This playlist exceeds {MAX_ENTRIES} tracks.')
    return title, tracks, warning
