"""
statcan_http.py
----------------
Shared HTTP helper for calling Statistics Canada's Web Data Service
(WDS) API.

StatCan's edge (Akamai) runs bot-scoring that intermittently rejects
requests from a plain Python HTTP client with a 406 — even with a
full, browser-shaped header set (Accept, Referer, Origin, a real
Chrome User-Agent). That's because it's fingerprinting the TLS
handshake itself, not just the HTTP headers, and Python's stock
`requests`/urllib3 has a recognizably different TLS signature than a
real browser. `curl_cffi` impersonates an actual Chrome TLS
fingerprint, which reliably gets through.

Both statcan.py and statcan_geography.py call post_json() here
instead of using `requests` directly, so the rest of the codebase
doesn't need to know curl_cffi exists — failures still come back as
the standard library's requests.exceptions.RequestException, which is
what main.py's error handling already expects.
"""

import requests
from curl_cffi import requests as cf_requests
from curl_cffi.requests import exceptions as cf_exceptions

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-CA,en;q=0.9",
    "Referer": "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1810020501",
    "Origin": "https://www150.statcan.gc.ca",
}


def post_json(url: str, body, timeout: int = 30):
    """
    POSTs JSON to a StatCan WDS endpoint, impersonating a real Chrome
    browser's TLS fingerprint, and returns the parsed JSON response.

    Raises requests.exceptions.RequestException (the standard
    library's own, not curl_cffi's — they're different exception
    hierarchies) on any network or HTTP-status failure, so every
    caller can keep catching one familiar exception type regardless of
    which HTTP client is doing the actual work underneath.
    """
    try:
        resp = cf_requests.post(
            url, json=body, headers=_HEADERS, timeout=timeout, impersonate="chrome124",
        )
        resp.raise_for_status()
        return resp.json()
    except cf_exceptions.RequestException as e:
        raise requests.exceptions.RequestException(str(e)) from e
