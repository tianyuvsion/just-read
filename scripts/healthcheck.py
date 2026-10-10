"""Container liveness probe; reuse its anonymous session instead of creating one per check."""
from http.cookiejar import LWPCookieJar, LoadError
from pathlib import Path
import json
import sys
from urllib.error import URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main() -> int:
    jar = LWPCookieJar(str(Path('/tmp/justread-health.cookies')))
    try:
        jar.load(ignore_discard=True)
    except (OSError, LoadError):
        pass
    try:
        # The fixed loopback endpoint is HTTP even when public cookies are Secure.
        # Reuse only this probe's session; never send it to another host.
        cookie = '; '.join(f'{c.name}={c.value}' for c in jar
                           if c.name == 'justread_session' and not c.is_expired())
        request = Request('http://127.0.0.1:8000/api/v1/health',
                          headers={'Cookie': cookie} if cookie else {})
        with build_opener(HTTPCookieProcessor(jar)).open(
            request, timeout=3
        ) as response:
            payload = json.load(response)
        jar.save(ignore_discard=True)
        # Missing API credentials may make ready=false while the service is live.
        runtime = payload.get('runtime')
        workers_ok = not isinstance(runtime, dict) or runtime.get('healthy') is True
        return 0 if payload.get('status') == 'ok' and workers_ok else 1
    except (OSError, URLError, ValueError, AttributeError):
        return 1


if __name__ == '__main__':
    sys.exit(main())
