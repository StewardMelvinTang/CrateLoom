"""Entrypoint for the bundled engine; include a CA store for public HTTPS."""
import os
import sys
import certifi
from crateloom.__main__ import main

if __name__ == '__main__':
    os.environ.setdefault('SSL_CERT_FILE',certifi.where())
    if getattr(sys,'frozen',False):
        # A onefile build re-reads its own executable for every lazy import. Load
        # everything now, so replacing the app while it runs cannot break a request.
        from crateloom import errors
        errors.preload()
    main()
