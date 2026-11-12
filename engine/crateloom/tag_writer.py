"""Edit a copy, verify stream/timing and tags, back up, then atomically replace."""
import hashlib,json,os,shutil,subprocess,sys,tempfile,uuid
from pathlib import Path

def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def identity(path):
    probe=json.loads(subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)],check=True,capture_output=True,text=True,timeout=30).stdout)
    stream=next(s for s in probe['streams'] if s['codec_type']=='audio')
    signature=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-c','copy','-f','hash','-hash','sha256','-'],check=True,capture_output=True,text=True,timeout=600).stdout.strip()
    return signature,stream['codec_name'],int(stream.get('sample_rate',0)),float(probe['format']['duration'])

def edit(path,fields,artwork,write_artwork):
    import mutagen
    if mutagen.version_string!='1.48.1':raise ValueError('Install the pinned Mutagen 1.48.1 environment')
    from mutagen.id3 import ID3,TIT2,TPE1,TALB,TCON,TDRC,APIC
    from mutagen.flac import FLAC,Picture
    from mutagen.mp4 import MP4,MP4Cover
    audio=mutagen.File(path)
    if audio is None:raise ValueError('Unsupported tag format')
    if audio.tags is None:audio.add_tags()
    image=Path(artwork).read_bytes() if artwork else None
    mime='image/png' if image and image.startswith(b'\x89PNG') else 'image/jpeg'
    if isinstance(audio.tags,ID3):
        frames={'title':TIT2,'artist':TPE1,'album':TALB,'genre':TCON,'date':TDRC}
        version=audio.tags.version[1] if audio.tags.version[1] in (3,4) else 4
        for key,value in fields.items():
            frame=frames[key];audio.tags.delall(frame.__name__)
            if value:audio.tags.add(frame(encoding=3,text=[value]))
        if write_artwork:
            for key,frame in list(audio.tags.items()):
                if isinstance(frame,APIC) and frame.type==3:del audio.tags[key]
            if image:audio.tags.add(APIC(encoding=3,mime=mime,type=3,desc='Front cover',data=image))
        audio.save(v2_version=version)
        read=mutagen.File(path)
        for key,value in fields.items():
            frames_read=read.tags.getall(frames[key].__name__)
            actual=str(frames_read[0].text[0]) if frames_read else ''
            if actual!=value:raise ValueError('Tag read-back failed: '+key)
    elif isinstance(audio,MP4):
        keys={'title':'\xa9nam','artist':'\xa9ART','album':'\xa9alb','genre':'\xa9gen','date':'\xa9day'}
        for key,value in fields.items():
            if value:audio[keys[key]]=[value]
            else:audio.tags.pop(keys[key],None)
        if write_artwork:
            if image:audio['covr']=[MP4Cover(image,imageformat=MP4Cover.FORMAT_PNG if mime=='image/png' else MP4Cover.FORMAT_JPEG)]
            else:audio.tags.pop('covr',None)
        audio.save();read=mutagen.File(path)
        for key,value in fields.items():
            if str(read.get(keys[key],[''])[0])!=value:raise ValueError('Tag read-back failed: '+key)
    elif isinstance(audio,FLAC) or audio.__class__.__name__ in ('OggVorbis','OggOpus'):
        for key,value in fields.items():
            if value:audio[key]=[value]
            else:audio.tags.pop(key,None)
        if write_artwork:
            if not isinstance(audio,FLAC):raise ValueError('Cover writing currently supports MP3, AIFF, WAV, FLAC and M4A')
            keep=[p for p in audio.pictures if p.type!=3];audio.clear_pictures()
            for pic in keep:audio.add_picture(pic)
            if image:
                pic=Picture();pic.type=3;pic.mime=mime;pic.data=image;audio.add_picture(pic)
        audio.save();read=mutagen.File(path)
        for key,value in fields.items():
            if str(read.get(key,[''])[0])!=value:raise ValueError('Tag read-back failed: '+key)
    else:raise ValueError('Tag writing is unavailable for this audio format')
    if write_artwork:
        read=mutagen.File(path)
        if isinstance(read.tags,ID3):images=[frame.data for frame in read.tags.getall('APIC') if frame.type==3]
        elif isinstance(read,MP4):images=[bytes(cover) for cover in read.get('covr',[])]
        elif isinstance(read,FLAC):images=[pic.data for pic in read.pictures if pic.type==3]
        else:images=[]
        if images!=([image] if image else []):raise ValueError('Artwork read-back failed; original kept')

def run(recipe):
    source=Path(recipe['path']);expected=recipe['expected_hash']
    if not source.is_file() or source.is_symlink() or source.stat().st_nlink!=1:raise ValueError('Tag writing requires a regular file without hard links')
    if not source.stat().st_mode & 0o222:raise ValueError('The audio file is read-only')
    if digest(source)!=expected:raise ValueError('File changed before writing; reload and review it again')
    original=identity(source)
    fd,name=tempfile.mkstemp(prefix='.crateloom-tags-',suffix=source.suffix,dir=source.parent);os.close(fd);temporary=Path(name)
    try:
        shutil.copy2(source,temporary);edit(temporary,recipe['fields'],recipe.get('artwork_path'),recipe.get('write_artwork',False))
        verified=identity(temporary)
        if verified[:3]!=original[:3] or abs(verified[3]-original[3])>.001:raise ValueError('Tag edit changed audio identity or timing; original kept')
        if digest(source)!=expected:raise ValueError('File changed during tag writing; original kept')
        backups=Path(recipe['backup_root']);backups.mkdir(parents=True,exist_ok=True);backup=backups/(uuid.uuid4().hex+source.suffix)
        shutil.copy2(source,backup)
        if digest(backup)!=expected:raise ValueError('Backup verification failed; original kept')
        for path in (backup,temporary):
            with path.open('rb') as f:os.fsync(f.fileno())
        if digest(source)!=expected:raise ValueError('File changed before replacement; original kept')
        new_hash=digest(temporary);os.replace(temporary,source)
        return {'file_hash':new_hash,'backup':str(backup)}
    finally:temporary.unlink(missing_ok=True)

if __name__=='__main__':
    output=Path(sys.argv[2]);output.parent.mkdir(parents=True,exist_ok=True)
    try:result=run(json.loads(Path(sys.argv[1]).read_text()));code=0
    except Exception as error:result={'error':str(error)};code=1
    output.write_text(json.dumps(result));raise SystemExit(code)
