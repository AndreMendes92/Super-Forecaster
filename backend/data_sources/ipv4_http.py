"""
ipv4_http.py
-------------
Shared "force IPv4" HTTP client for the livability tab's non-StatCan
external calls (OpenStreetMap's Overpass API, CMHC).

Render's outbound networking doesn't reliably support IPv6 — curl can
try an IPv6 route first and fail with "Network is unreachable" even
though an IPv4 route works fine. This exact symptom already bit this
app once before (see the long comment in statcan_http.py, which also
hit it with Supabase) — this module applies the same fix
(curl_cffi with IPRESOLVE forced to IPv4-only) to calls that don't
need StatCan's Chrome-impersonation headers, just the IPv4 fix.
"""

from curl_cffi import requests as cf_requests
from curl_cffi.const import CurlOpt

_IPV4_ONLY = {CurlOpt.IPRESOLVE: 1}


def get(url: str, params: dict = None, headers: dict = None, timeout: int = 30):
    return cf_requests.get(url, params=params, headers=headers, timeout=timeout, curl_options=_IPV4_ONLY)


def post(url: str, data=None, headers: dict = None, timeout: int = 30):
    return cf_requests.post(url, data=data, headers=headers, timeout=timeout, curl_options=_IPV4_ONLY)
