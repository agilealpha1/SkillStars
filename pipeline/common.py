"""Shared helpers: paths, atomic file writes, and a GitHub HTTP client with rate-limit backoff.

Standard library plus `requests`. Every writer goes through write_json/write_jsonl, which write to a temp
file and rename, so a crash mid-step never leaves a half-written data file behind (the previous one stays).
"""
import hashlib, json, os, re, sys, tempfile, time, urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
DATA = os.path.join(ROOT, 'data')
CACHE = os.path.join(ROOT, '.cache')          # re-fetchable HTTP cache, git-ignored
MIN_STARS = int(os.environ.get('SS_MIN_STARS', '10000'))


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, file=sys.stderr, flush=True)


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def _atomic(path, text):
    """Write text to path via a temp file + rename. mkstemp creates 0600 files; make them world-readable (0644)
    like any normally created file, so they're servable and diff cleanly in git."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path, obj, indent=None):
    _atomic(path, json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=indent is not None) + '\n')


def write_jsonl(path, rows):
    _atomic(path, ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows))


def sha(text):
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()[:16]


_SCHEME = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*:')
_BARE_HOST = re.compile(r'^(?://)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:\d+)?([/?#]\S*)?$')


def safe_url(url):
    """Return an http(s) URL safe to put in an href, or None.

    GitHub `homepage` is free text set by repo owners: it can be a bare domain ("flashml.ai", which a browser would
    resolve relative to the site) or a `javascript:`/`data:` URI (script execution on click). Bare domains get
    https://, anything that isn't http/https with a host is dropped."""
    u = (url or '').strip()
    if not u or any(c in u for c in ' \t\r\n<>"\'`\\'):
        return None
    if _BARE_HOST.match(u):                 # before the scheme test: "example.com:8080" is a host with a port,
        u = 'https://' + u.lstrip('/')      # not a URL with scheme "example.com"
    elif not _SCHEME.match(u):
        return None
    try:
        parts = urllib.parse.urlsplit(u)
    except ValueError:
        return None
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
        return None
    return u


class GitHub:
    """Tiny GitHub REST/GraphQL client. Works without a token (10 searches/min, no GraphQL) and with one
    (30 searches/min, GraphQL). Sleeps through primary and secondary rate limits instead of failing."""

    API = 'https://api.github.com'

    def __init__(self, token=None, session=None, max_retries=6):
        import requests
        self.token = token if token is not None else (os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN') or '')
        self.s = session or requests.Session()
        self.s.headers.update({'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                               'User-Agent': 'SkillStars-pipeline'})
        if self.token:
            self.s.headers['Authorization'] = f'Bearer {self.token}'
        self.max_retries = max_retries
        self.calls = 0

    def _wait(self, r, attempt):
        ra = r.headers.get('Retry-After')
        if ra:
            return float(ra) + 1
        if r.headers.get('X-RateLimit-Remaining') == '0' and r.headers.get('X-RateLimit-Reset'):
            return max(1.0, float(r.headers['X-RateLimit-Reset']) - time.time() + 2)
        return min(120, 5 * 2 ** attempt)

    def request(self, method, url, **kw):
        import requests
        url = url if url.startswith('http') else self.API + url
        for attempt in range(self.max_retries):
            try:
                r = self.s.request(method, url, timeout=60, **kw)
            except requests.RequestException as e:
                wait = min(120, 5 * 2 ** attempt)
                log(f'network error {e.__class__.__name__}; retry in {wait:.0f}s')
                time.sleep(wait); continue
            self.calls += 1
            if r.status_code in (403, 429) and ('rate limit' in r.text.lower() or r.headers.get('X-RateLimit-Remaining') == '0'
                                                 or r.headers.get('Retry-After')):
                wait = self._wait(r, attempt)
                log(f'rate limited ({r.status_code}); sleeping {wait:.0f}s')
                time.sleep(wait); continue
            if r.status_code >= 500 or r.status_code == 422 and 'timed out' in r.text.lower():
                wait = min(120, 5 * 2 ** attempt)
                log(f'HTTP {r.status_code}; retry in {wait:.0f}s')
                time.sleep(wait); continue
            # proactive pacing: if this was the last call in the window, wait for the reset now
            if r.headers.get('X-RateLimit-Remaining') == '0' and r.headers.get('X-RateLimit-Reset'):
                wait = max(0.0, float(r.headers['X-RateLimit-Reset']) - time.time() + 1)
                if wait < 3700:
                    log(f'rate window used up; waiting {wait:.0f}s for reset')
                    time.sleep(wait)
            return r
        raise RuntimeError(f'GitHub request failed after {self.max_retries} attempts: {method} {url}')

    def get(self, url, **kw):
        return self.request('GET', url, **kw)

    def graphql(self, query, variables=None):
        if not self.token:
            raise RuntimeError('GraphQL needs GITHUB_TOKEN')
        r = self.request('POST', '/graphql', json={'query': query, 'variables': variables or {}})
        r.raise_for_status()
        out = r.json()
        if out.get('errors') and not out.get('data'):
            raise RuntimeError(f"GraphQL error: {out['errors'][:2]}")
        return out.get('data') or {}
