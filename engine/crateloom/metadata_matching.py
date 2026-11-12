"""Conservative text matching. A search score is never audio identification."""
import math
import re
import unicodedata
from difflib import SequenceMatcher

VERSION_WORD = re.compile(r'\b(remix|mix|edit|bootleg|mashup|rework|dub|instrumental|acoustic|live|extended|radio|original|remaster(?:ed)?)\b', re.I)
NOISE = r'(?:official\s+(?:audio|music\s+video|video|visuali[sz]er)|lyrics?(?:\s+video)?|audio\s+only|hd|hq|4k|klikkaud|klickaud)'
GENERIC_VERSION = set('remix mix edit bootleg mashup rework dub instrumental acoustic live extended radio original remaster remastered version club jersey'.split())
VERSION_PHRASE = r'(?:original|extended|radio|club|instrumental|acoustic|live|remaster(?:ed)?)'
# "Song - Live" or "Song - 2011 Remaster": after a separator a version word is explicit.
DASH_VERSION = re.compile(r'\s+[-–—]\s+((?:[0-9]{4}\s+)?' + VERSION_PHRASE + r'(?:\s+(?:mix|edit|version))?(?:\s+[0-9]{4})?)$', re.I)
# Without a delimiter only a full phrase counts: "In Da Club", "Born to Live"
# and "The Original" are titles, "Song Club Mix" and "Song Radio Edit" are versions.
PHRASE_VERSION = re.compile(r'\s+(' + VERSION_PHRASE + r'\s+(?:mix|edit|version)|instrumental)$', re.I)
FEATURE = re.compile(r'\s*[\[(]\s*(?:feat\.?|ft\.?|featuring)\s+[^\])]+[\])]\s*|\s+(?:feat\.?|ft\.?|featuring)\s+.+$', re.I)


def primary_artist(value):
    # Preserve the full credit in metadata; use the leading credit for retrieval.
    # "Tyler, The Creator" is one name: a comma before a closing "The <Word>"
    # does not separate two credits.
    return re.split(r'\s*(?:,(?!\s*the\s+[^\s,;&]+\s*(?:$|[,;&]|(?:feat\.?|ft\.?|featuring)\s))|;|\bfeat\.?\s|\bft\.?\s|\bfeaturing\s)\s*', value, maxsplit=1, flags=re.I)[0].strip()


def base_title(value):
    return re.sub(r'\s+', ' ', FEATURE.sub(' ', value)).strip()


def normalized(value):
    value = unicodedata.normalize('NFKD', value).casefold()
    return ' '.join(re.findall(r'[^\W_]+', ''.join(c for c in value if not unicodedata.combining(c))))


def clean_name(value):
    value = unicodedata.normalize('NFKC', value).replace('_', ' ')
    value = re.sub(r'\.(?:mp3|flac|wav|aiff?|m4a|ogg|opus)$', '', value, flags=re.I)
    value = re.sub(r'[\[(]\s*' + NOISE + r'\s*[\])]', '', value, flags=re.I)
    value = re.sub(r'\b(?:klickaud|klikkaud)\b', '', value, flags=re.I)
    value = re.sub(r'\s+' + NOISE + r'\s*$', '', value, flags=re.I)
    return re.sub(r'\s+', ' ', value).strip(' -–—')


def split_version(title):
    versions = []
    def bracket(match):
        if VERSION_WORD.search(match[1]):
            versions.append(match[1].strip())
            return ' '
        return match[0]
    base = re.sub(r'[\[(]([^\]\)]+)[\])]', bracket, title)
    # Explicit version suffixes are safe to split. A single unbracketed
    # remixer name is a search hint, not an artist credit to write to a file.
    suffix = DASH_VERSION.search(base) or PHRASE_VERSION.search(base)
    if not suffix:
        suffix = re.search(r'\s+((?:\S+\s+)?(?:jersey\s+club\s+)?(?:remix|bootleg|mashup|rework))$', base, re.I)
        if suffix and not base[:suffix.start()].strip():
            suffix = re.search(r'\s+(remix|bootleg|mashup|rework)$', base, re.I)
    if suffix:
        versions.append(suffix[1]); base = base[:suffix.start()]
    return re.sub(r'\s+', ' ', base).strip(' -–—'), ' / '.join(versions)


def search_identity(title, artist=''):
    title, artist = clean_name(title), clean_name(artist)
    parts = re.split(r'\s+[-–—]\s+', title, maxsplit=1)
    if len(parts) == 2:
        left, right = parts
        if artist and normalized(left) == normalized(artist):
            title = right
        elif not artist:
            if re.search(r'\bjersey$', left, re.I) and VERSION_WORD.search(right):
                # Title-first download names have ambiguous artist credits.
                # Keep those credits out of the artist field.
                title = re.sub(r'\s+jersey$', '', left, flags=re.I)
                _, version = split_version(right)
                return {'title': title, 'artist': '', 'version': version or right}
            artist, title = left, right
    base, version = split_version(title)
    return {'title': base or title, 'artist': artist, 'version': version}


def catalog_identity(title, artist=''):
    identity = search_identity(title, artist)
    identity['title'] = base_title(identity['title']) or identity['title']
    return identity


def similarity(left, right):
    left, right = normalized(left), normalized(right)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    a, b = set(left.split()), set(right.split())
    return max(SequenceMatcher(None, left, right).ratio(), len(a & b) / max(len(a), len(b)))


def query_plan(identity, duration):
    quote = lambda text: '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    base = 'recording:' + quote(identity['title'])
    credited = base + (' AND artist:' + quote(primary_artist(identity['artist'])) if identity['artist'] else '')
    version = identity['version']
    # Keep the words in their written order: the term is quoted as a phrase.
    specific = [word for word in dict.fromkeys(normalized(version).split()) if word not in GENERIC_VERSION]
    version_term = ' '.join(specific) or version
    precise = credited
    if version_term:
        precise += ' AND (recording:' + quote(version_term) + ' OR comment:' + quote(version_term) + ')'
    if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0:
        tolerance = max(12, duration * .08)
        precise += f' AND dur:[{max(0, int((duration-tolerance)*1000))} TO {int((duration+tolerance)*1000)}]'
    return list(dict.fromkeys([precise, credited, base]))


def rank_candidates(payload, duration, identity):
    result = []
    for recording in payload.get('recordings', []):
        artist = ''.join(x.get('name', x.get('artist', {}).get('name', '')) + x.get('joinphrase', '') for x in recording.get('artist-credit', []) if isinstance(x, dict))
        title = recording.get('title', '')
        base, title_version = split_version(clean_name(title))
        comment = recording.get('disambiguation') or ''
        version = ' / '.join(filter(None, [title_version, comment if VERSION_WORD.search(comment) else '']))
        expected = normalized(identity['version'])
        actual = normalized(version)
        title_score = similarity(base_title(identity['title']), base_title(base))
        artist_score = max(similarity(identity['artist'], artist), similarity(primary_artist(identity['artist']), primary_artist(artist))) if identity['artist'] else .5
        length = recording.get('length')
        seconds = length / 1000 if isinstance(length, (int, float)) and math.isfinite(length) and length > 0 else None
        difference = round(abs(seconds-duration), 2) if seconds is not None and isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0 else None
        ewords, awords = set(expected.split()), set(actual.split())
        reasons = []
        if title_score < .65: reasons.append('Different title')
        if identity['artist'] and artist_score < .6: reasons.append('Different artist')
        expected_names = set(expected.split()) - GENERIC_VERSION
        actual_names = set(actual.split()) - GENERIC_VERSION
        remix_words = {'remix', 'bootleg', 'mashup', 'rework'}
        if expected_names and not expected_names.issubset(actual_names):
            reasons.append('Requested remix not identified' if ewords & {'remix', 'bootleg', 'mashup', 'rework'} else 'Requested version not identified')
        elif expected and not actual:
            reasons.append('Requested version not identified')
        elif (ewords & remix_words and not awords & remix_words) or (not ewords & remix_words and awords & remix_words):
            reasons.append('Different remix')
        elif ('live' in ewords) != ('live' in awords):
            reasons.append('Different live/studio version')
        elif any((word in ewords) != (word in awords) for word in ('instrumental', 'acoustic', 'extended', 'radio', 'dub')):
            reasons.append('Different version')
        if difference is not None and difference > max(12, duration * .08): reasons.append('Duration differs too much')
        strong = title_score >= .95 and bool(identity['artist']) and artist_score >= .9 and difference is not None and difference <= 3 and (expected == actual or not expected and not actual)
        status = 'rejected' if reasons else 'strong' if strong else 'review'
        rank = round(60*title_score + 25*artist_score + (10*max(0, 1-difference/20) if difference is not None else 0) + (5 if expected == actual else 0), 2)
        releases = recording.get('releases') or [{}]
        def release_order(release):
            group = release.get('release-group') or {}
            return (release.get('status', '') != 'Official', 'Compilation' in group.get('secondary-types', []), release.get('date') or '9999', release.get('title', ''))
        for release in sorted(releases, key=release_order)[:3]:
            group = release.get('release-group') or {}
            compilation = 'Compilation' in group.get('secondary-types', [])
            release_penalty = (12 if release.get('status') == 'Bootleg' else 0) + (6 if compilation else 0)
            release_kind = ' · '.join(filter(None, [release.get('status'), 'Compilation' if compilation else group.get('primary-type')]))
            display_title = title + (' (' + comment + ')' if comment and VERSION_WORD.search(comment) and normalized(comment) not in normalized(title) else '')
            result.append({'provider': 'MusicBrainz', 'recording_id': recording.get('id'), 'release_id': release.get('id'),
                'values': {'title': display_title, 'artist': artist, 'album': release.get('title', ''), 'date': release.get('date', ''), 'genre': ''},
                'version': version or comment, 'duration': seconds, 'duration_difference': difference,
                'score': recording.get('score'), 'rank': rank-release_penalty, 'title_similarity': title_score, 'artist_similarity': artist_score, 'match_status': status, 'reasons': reasons, 'release_kind': release_kind, 'release_country': release.get('country', ''), 'release_format': ' / '.join(dict.fromkeys(m.get('format', '') for m in release.get('media', []) if m.get('format'))),
                'source_url': 'https://musicbrainz.org/recording/' + str(recording.get('id', ''))})
    return sorted(result, key=lambda c: (c['match_status'] == 'rejected', -c['rank']))
