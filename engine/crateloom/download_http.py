"""Bounded public HTTPS requests, including validation on every redirect."""
import email.message
from email.utils import parsedate_to_datetime
import http.client
import ipaddress
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 512 * 1024 * 1024
TIMEOUT = 10
USER_AGENT = 'CrateLoom/0.1.0 (https://github.com/StewardMelvinTang/CrateLoom)'


class SourceHTTPError(ValueError):
    def __init__(self, code, message, retry_after=None):
        super().__init__(message)
        self.code = code
        try:
            self.retry_after = float(retry_after) if retry_after.isdecimal() else parsedate_to_datetime(retry_after).timestamp() - time.time()
        except (AttributeError, ValueError, TypeError, OverflowError):
            self.retry_after = None


class SourceConnectionError(ValueError):
    pass


def public_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or any(ord(c) < 32 for c in value):
        raise ValueError('Enter a valid HTTPS download URL')
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii')
        if '%' in host or '\\' in host:
            raise ValueError()
        try:
            if not ipaddress.ip_address(host).is_global:
                raise ValueError('Choose a public HTTPS audio URL')
        except ValueError as error:
            if 'Choose a public' in str(error):
                raise
        return urllib.parse.urlunsplit(('https', '['+host+']' if ':' in host else host, urllib.parse.quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~"), parsed.query, ''))
    except (ValueError, UnicodeError) as error:
        raise ValueError('Choose a public HTTPS URL on port 443, without a login in the URL') from error


def display_url(value):
    parsed = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


class PublicConnection(http.client.HTTPSConnection):
    def connect(self):
        # Connect to the validated address itself, retaining the hostname for
        # certificate/SNI checks; a second DNS lookup cannot rebind the request.
        addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)))
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError('The source resolves to a private or unsupported address')
        deadline = time.monotonic()+TIMEOUT
        for ip in addresses:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = socket.create_connection((ip, self.port), min(TIMEOUT, remaining))
                try:
                    self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                except Exception:
                    raw.close()
                    raise
                return
            except OSError:
                continue
        raise TimeoutError('The source could not be reached')


class PublicHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        public_url(req.full_url)
        return self.do_open(PublicConnection, req, context=ssl.create_default_context())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, public_url(newurl))


def open_url(url, method='GET', headers=None):
    req = urllib.request.Request(public_url(url), method=method, headers={
        'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity', **(headers or {})})
    # No implicit credentials, cookies or system proxy access.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicHTTPS(), PublicRedirect())
    try:
        return opener.open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:
        error.close()
        retry_after = error.headers.get('Retry-After') if error.headers else None
        if error.code in (401, 403):
            raise SourceHTTPError(error.code, 'This source requires access or the download link expired. Resolve a fresh download URL.', retry_after) from error
        if error.code == 412:
            raise SourceHTTPError(error.code, 'The source changed after review. Resolve the link again.', retry_after) from error
        raise SourceHTTPError(error.code, f'The source returned HTTP {error.code}. Check the download URL or retry later.', retry_after) from error
    except (OSError, urllib.error.URLError) as error:
        raise SourceConnectionError('The source could not be reached. Check the connection and retry.') from error


def content_info(response, original_url):
    mime = response.headers.get_content_type() if response.headers.get('Content-Type') else 'application/octet-stream'
    raw_size = response.headers.get('Content-Length')
    size = int(raw_size) if raw_size and raw_size.isdigit() else None
    if size is not None and size > MAX_BYTES:
        raise ValueError('This download exceeds the 512 MB limit')
    message = email.message.Message()
    message['Content-Disposition'] = response.headers.get('Content-Disposition', '')
    filename = message.get_filename() or urllib.parse.unquote(urllib.parse.urlsplit(response.url).path.rsplit('/', 1)[-1])
    filename = re.split(r'[/\\]', filename)[-1]
    filename = re.sub(r'[\x00-\x1f\x7f]', '', filename).strip()[:180] or 'Audio download'
    if mime in ('text/html', 'application/xhtml+xml', 'application/json', 'text/plain'):
        raise ValueError('This is a web page, not an audio file. Paste the artist’s direct audio download link.')
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if not mime.startswith('audio/') and extension not in ('mp3', 'wav', 'aiff', 'aif', 'm4a', 'flac', 'ogg'):
        raise ValueError('The link does not expose a supported audio download')
    return {'url': original_url, 'resolved_url': public_url(response.url), 'filename': filename,
            'mime': mime, 'declared_bytes': size, 'etag': response.headers.get('ETag'),
            'last_modified': response.headers.get('Last-Modified')}


def inspect(url):
    # Some download endpoints don't implement HEAD. A bounded range GET then
    # reads only headers; the body is immediately closed.
    request_url = public_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicHTTPS(), PublicRedirect())
    req = urllib.request.Request(request_url, method='HEAD', headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'})
    try:
        with opener.open(req, timeout=TIMEOUT) as response:
            return content_info(response, request_url)
    except urllib.error.HTTPError as error:
        error.close()
        if error.code not in (405, 501):
            with open_url(request_url) as response:
                return content_info(response, request_url)
    with open_url(request_url, headers={'Range': 'bytes=0-0'}) as response:
        info = content_info(response, request_url)
        # Content-Length on a partial response describes the range, not the file.
        if response.status == 206:
            total = response.headers.get('Content-Range', '').rsplit('/', 1)[-1]
            info['declared_bytes'] = int(total) if total.isdigit() else None
            if info['declared_bytes'] is not None and info['declared_bytes'] > MAX_BYTES:
                raise ValueError('This download exceeds the 512 MB limit')
        return info
