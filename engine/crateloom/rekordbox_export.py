"""Ordered XML export with portable cue data and optional verified audio copies."""
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET


def location(path):
    uri = path.as_uri()
    return 'file://localhost/'+uri[len('file:///'):] if uri.startswith('file:///') else uri


def xml_text(value):
    # XML 1.0 forbids most ASCII controls even in escaped attributes.
    return re.sub(r'[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]', '\ufffd', value)


def reviewed_grid(track):
    bpm = track.get('grid_bpm')
    origin = track.get('grid_origin_us')
    anchor = track.get('grid_anchor_us')
    if bpm is None or origin is None or anchor is None or not 20 <= bpm <= 400:
        return None
    interval = 60_000_000 / bpm
    beat = round((anchor - origin) / interval)
    if abs(anchor - (origin + beat * interval)) > 1000:
        return None
    downbeat = origin + beat * interval + track['grid_offset_us']
    if not math.isfinite(downbeat) or not 0 <= downbeat < track['duration'] * 1_000_000:
        return None
    return {'Inizio': f'{downbeat / 1_000_000:.6f}', 'Bpm': str(bpm), 'Metro': '4/4', 'Battito': '1'}


def document(tracks, paths, name, version):
    root = ET.Element('DJ_PLAYLISTS', Version='1.0.0')
    ET.SubElement(root, 'PRODUCT', Name='CrateLoom', Version=version, Company='CrateLoom')
    collection = ET.SubElement(root, 'COLLECTION', Entries=str(len(tracks)))
    folder = ET.SubElement(ET.SubElement(root,'PLAYLISTS'),'NODE',Type='0',Name='ROOT',Count='1')
    playlist = ET.SubElement(folder,'NODE',Type='1',Name=xml_text(name),KeyType='0',Entries=str(len(tracks)))
    for index,(track,path) in enumerate(zip(tracks,paths),1):
        key = str(index)
        attrs = dict(TrackID=key,Name=xml_text(track['title']),Artist=xml_text(track['artist']),
                     Location=location(path),TotalTime=str(int(track['duration'])))
        values = (track.get('metadata') or {}).get('values') or {}
        for field, attr in [('album','Album'),('genre','Genre')]:
            if values.get(field):
                attrs[attr] = xml_text(values[field])
        grid = reviewed_grid(track)
        if grid:
            attrs['AverageBpm'] = grid['Bpm']
        item = ET.SubElement(collection,'TRACK',**attrs)
        if grid:
            ET.SubElement(item, 'TEMPO', **grid)
        for cue in track['cues']:
            seconds,micros = divmod(cue['position_us'],1_000_000)
            mark = dict(Name=xml_text(cue['label']),Type='0',Start=f'{seconds}.{micros:06}',Num=str(cue['slot']))
            if cue.get('color'):
                mark.update({channel:str(int(cue['color'][i:i+2],16)) for channel,i in [('Red',1),('Green',3),('Blue',5)]})
            ET.SubElement(item,'POSITION_MARK',**mark)
        ET.SubElement(playlist,'TRACK',Key=key)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def publish(source, dest, created):
    """Hard-link staged data when possible; use exclusive creation on FAT volumes."""
    try:
        os.link(source,dest)
        created.append(dest)
    except OSError as error:
        if error.errno not in (errno.EPERM,errno.EACCES,errno.ENOTSUP,errno.EOPNOTSUPP,errno.EXDEV,errno.ENOSYS):
            raise
        with dest.open('xb') as output:
            created.append(dest)
            with source.open('rb') as data:
                shutil.copyfileobj(data,output,1024*1024)
            output.flush()
            os.fsync(output.fileno())


def export(api,db,params):
    selectors = [key for key in ('id','track_ids','playlist_id') if key in params]
    if len(selectors)!=1:
        raise ValueError('Choose one track, a selection, or a playlist')
    name = 'CrateLoom'
    if 'playlist_id' in params:
        row = db.execute('SELECT name FROM playlists WHERE id=?',(params['playlist_id'],)).fetchone()
        if row is None:
            raise ValueError('Playlist no longer exists')
        name = row['name']
        ids = [r['track_id'] for r in db.execute('SELECT track_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position',(params['playlist_id'],))]
    else:
        ids = [params['id']] if 'id' in params else params['track_ids']
    if not isinstance(ids,list) or not 1<=len(ids)<=5000 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
        raise ValueError('Choose 1–5,000 distinct tracks to export')
    copy = params.get('copy_audio',False)
    if type(copy) is not bool:
        raise ValueError('Invalid audio copy setting')
    dest = Path(params['path']).expanduser().absolute()
    dest = dest.parent.resolve()/dest.name
    if dest.suffix.lower()!='.xml' or not dest.parent.is_dir():
        raise ValueError('Choose an XML file in an existing folder')
    backup = dest.with_suffix('.crateloom.json')
    manifest = dest.with_suffix('.manifest.json')
    audio_dir = dest.with_name(dest.stem+'-audio') if copy else None
    targets = [dest,backup,manifest]+([audio_dir] if copy else [])
    protected = {Path(r['path']).resolve() for r in db.execute('SELECT path FROM tracks')}
    if any(target.resolve() in protected for target in targets):
        raise ValueError('Choose an export destination separate from library audio')
    if any(os.path.lexists(target) for target in targets):
        raise ValueError('Export files already exist. Choose a new export name.')
    tracks, originals = [], []
    for tid in ids:
        track = api.track(db,tid)
        try:
            source = api.verify_asset(db,track).resolve()
        except (ValueError,OSError) as error:
            raise ValueError(f'{track["title"]}: {error}') from error
        tracks.append(api.track(db,tid))
        originals.append(source)
    if copy and shutil.disk_usage(dest.parent).free < sum(p.stat().st_size for p in originals):
        raise ValueError('There is not enough free space for the audio copies')
    created = []
    made_audio_dir = False
    with tempfile.TemporaryDirectory(prefix='.crateloom-export-',dir=dest.parent) as temp:
        stage = Path(temp)
        try:
            paths = originals
            if copy:
                paths = []
                for index,(track,source) in enumerate(zip(tracks,originals),1):
                    safe = re.sub(r'[^\w .()-]','_',source.stem).strip(' .').encode('utf-8')[:120].decode('utf-8','ignore') or 'Track'
                    name_on_disk = f'{index:04d}-{safe}{source.suffix.lower()}'
                    staged = stage/name_on_disk
                    shutil.copyfile(source,staged)
                    if api.file_hash(staged)!=track['file_hash']:
                        raise ValueError(f'{track["title"]}: audio changed during export')
                    paths.append(audio_dir/name_on_disk)
            xml = document(tracks,paths,name,api.VERSION)
            clean_tracks = [{k:v for k,v in t.items() if k not in ('analysis','detail_analysis','structure_analysis','missing')} for t in tracks]
            for clean,full in zip(clean_tracks,tracks):
                if full.get('detail_analysis'):
                    clean['analysis_summary'] = {k:v for k,v in full['detail_analysis'].items() if k!='cache_path'}
            backup_doc = {'format_version':2,'playlist':name,'tracks':clean_tracks}
            # Keep the original single-track backup reader contract.
            if 'id' in params:
                backup_doc = {'format_version':1,'track':tracks[0]}
            manifest_doc = {'format_version':1,'playlist':name,'xml':dest.name,'backup':backup.name,'copied_audio':copy,
                            'xml_sha256':hashlib.sha256(xml).hexdigest(),
                            'tracks':[{'key':i,'id':t['id'],'location':location(p),'relative_audio':p.relative_to(dest.parent).as_posix() if copy else None,'file_sha256':t['file_hash'],'cue_count':len(t['cues'])} for i,(t,p) in enumerate(zip(tracks,paths),1)]}
            staged_outputs = [(stage/'_backup.json',backup),(stage/'_manifest.json',manifest),(stage/'_playlist.xml',dest)]
            for (tmp,_),data in zip(staged_outputs,[json.dumps(backup_doc,ensure_ascii=False,indent=2).encode(),json.dumps(manifest_doc,ensure_ascii=False,indent=2).encode(),xml]):
                with tmp.open('xb') as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            if copy:
                audio_dir.mkdir()
                made_audio_dir = True
                for path in paths:
                    publish(stage/path.name,path,created)
            for source,target in staged_outputs:
                publish(source,target,created)
        except BaseException:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            if made_audio_dir:
                audio_dir.rmdir()
            raise
    return {'xml':str(dest),'backup':str(backup),'manifest':str(manifest),'audio_directory':str(audio_dir) if copy else None,'track_count':len(tracks),
            'grid_count':sum(reviewed_grid(t) is not None for t in tracks)}
