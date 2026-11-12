"""Entrypoint for the bundled engine; include a CA store for public HTTPS."""
import os
import certifi
from crateloom.__main__ import main

if __name__ == '__main__':
    os.environ.setdefault('SSL_CERT_FILE',certifi.where())
    main()
