"""
_osm_extract_worker.py
-------------------------
Standalone worker process for the OSM extract download + parse (see
livability_osm_extract.py, which launches this via subprocess.run()
rather than calling it in-process).

Why a subprocess: osmium and shapely are C-extension libraries, and
their behavior under Render's exact deployed Python version has never
been independently verified in this app (this session's own sandbox
testing of them was done under Python 3.11.15; Render's build log has
shown Python 3.14.3 — a very new release those wheels may not fully
support). Real production evidence points at a hard crash somewhere in
this pass: three consecutive refresh runs, on a confirmed-deployed
commit, all left the cache's `meta` timestamp completely untouched —
not even updated once for the *first* municipality in the loop, even
though livability_cache.py's refresh_all() writes `meta` after every
single municipality. The OSM fetch runs before that loop starts, so
the only way to get zero writes, across three separate attempts, is
for the whole backend process to die *before* the loop — consistent
with a segfault or an OOM-kill (Render's free tier has a 512MB limit;
a full metro-area .osm.pbf plus osmium/shapely parsing is a real
memory risk), neither of which a Python try/except can catch, since
both kill the process instead of raising an exception.

Running this in a child process instead means the crash can only take
down the child. The parent (livability_osm_extract.fetch_all_osm_counts)
treats a non-zero exit, a timeout, or unparseable output as this one
source failing for this run — same as any other _fetch_with_fallback
failure — and refresh_all() carries on to crime/population/income/rent
and the per-municipality loop regardless, instead of the entire backend
process silently disappearing mid-refresh.

Talks to its parent over stdin/stdout as plain JSON, and adds this
file's own parent directory to sys.path itself, so it works whether
launched as a bare script (`sys.executable this_file.py`, which is how
livability_osm_extract.py runs it) or, for local debugging, from a
different cwd.

stdin:  {municipality_id: GeoJSON geometry dict}
stdout: {municipality_id: {"walkability": int, "transit": int, "green_space": int}}
        on one line, on success, then exits 0.
        Any failure prints a traceback to stderr and exits 1 — a crash
        with no output at all (segfault, OOM-kill) looks the same to
        the parent: a non-zero/abnormal exit, treated as failure.
"""
import json
import os
import sys
import tempfile
import traceback

# Allow `from data_sources...` imports regardless of cwd — this file
# lives in backend/data_sources/, so its grandparent dir is backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    boundaries = json.loads(sys.stdin.read())

    import shapely.geometry as sgeom
    from shapely.prepared import prep

    from data_sources.livability_osm_extract import BBBIKE_EXTRACT_URL, _CountingHandler
    from data_sources import ipv4_http
    import osmium

    polygons = {}
    for municipality_id, geometry in boundaries.items():
        try:
            polygons[municipality_id] = prep(sgeom.shape(geometry))
        except Exception:
            continue
    if not polygons:
        print("No usable municipality boundary polygons to count OSM features against.", file=sys.stderr)
        sys.exit(1)

    resp = ipv4_http.get(BBBIKE_EXTRACT_URL, timeout=120)
    resp.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".osm.pbf") as f:
        f.write(resp.content)
        f.flush()
        handler = _CountingHandler(polygons)
        osmium.apply(f.name, handler)

    sys.stdout.write(json.dumps(handler.counts))
    sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
