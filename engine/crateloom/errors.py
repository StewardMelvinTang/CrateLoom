"""Plain sentences for engine failures; the raw text is kept only as a detail."""
import os
import sqlite3
import sys
import zlib

from .library_imports import ImportCancelled

# Every engine module except __main__. The frozen sidecar imports these at
# startup, because a lazy import re-reads the executable after an update.
EAGER_MODULES = (
    'crateloom.acquisitions', 'crateloom.analysis_worker', 'crateloom.beat_grid', 'crateloom.cue_rules',
    'crateloom.download_http', 'crateloom.errors', 'crateloom.fast_sections', 'crateloom.library_imports',
    'crateloom.metadata_batch', 'crateloom.metadata_lookup', 'crateloom.metadata_matching', 'crateloom.playback',
    'crateloom.playlist_sources', 'crateloom.preparation', 'crateloom.providers', 'crateloom.rekordbox_export',
    'crateloom.scan_audio', 'crateloom.service_links', 'crateloom.set_artwork', 'crateloom.set_discovery',
    'crateloom.set_import', 'crateloom.set_scan_jobs', 'crateloom.shazam_fast', 'crateloom.shazam_scan',
    'crateloom.shazam_worker', 'crateloom.soundcloud_download', 'crateloom.structure_worker', 'crateloom.tag_writer',
    'crateloom.youtube_audio', 'crateloom.youtube_tracklist',
)
# Standard modules that engine code imports lazily.
EAGER_STDLIB = ('encodings.idna', 'encodings.punycode', 'concurrent.futures', 'http.cookiejar', 'email.utils',
                'xml.etree.ElementTree', 'html.parser', 'zipfile', 'ssl', 'uuid', 'hashlib', 'asyncio')
EXECUTABLE_ID = None
UPDATED = 'CrateLoom was updated while it was open. Restart CrateLoom to continue.'


def executable_identity(path=None):
    try:
        info = os.stat(path or sys.executable)
    except OSError:
        return None
    return (info.st_ino, info.st_size, info.st_mtime_ns)


def executable_replaced():
    return EXECUTABLE_ID is not None and executable_identity() != EXECUTABLE_ID


def detailed(message, detail=None):
    """A plain ValueError that carries diagnostic text for the job's details only."""
    error = ValueError(message)
    error.detail = detail[:500] if isinstance(detail, str) else None
    return error


def public_error(exc):
    detail = f'{type(exc).__name__}: {exc}'[:500]
    if executable_replaced():
        detail += ' (the app file changed)'
    if isinstance(exc, (ValueError, ImportCancelled)):
        extra = getattr(exc, 'detail', None)
        return str(exc), 'engine_error', extra if isinstance(extra, str) else None
    if isinstance(exc, zlib.error) or (isinstance(exc, ImportError) and getattr(sys, 'frozen', False)):
        return UPDATED, 'engine_updated', detail
    if isinstance(exc, sqlite3.OperationalError) and 'locked' in str(exc):
        return 'CrateLoom was busy. Try again.', 'busy', detail
    if isinstance(exc, FileNotFoundError):
        return 'A file CrateLoom needed is missing.', 'engine_error', detail
    if isinstance(exc, OSError) and str(exc).startswith('[Errno'):
        return 'A file or network operation failed. Try again.', 'engine_error', detail
    return 'CrateLoom hit an internal error. Try again.', 'internal', detail


def preload():
    """Import the engine and its lazily used standard modules while the executable is unchanged."""
    import importlib
    global EXECUTABLE_ID
    EXECUTABLE_ID = executable_identity()
    for name in EAGER_STDLIB + EAGER_MODULES:
        try:
            importlib.import_module(name)
        except ImportError:
            # An optional dependency never stops startup.
            pass
