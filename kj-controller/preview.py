"""Browser preview service: resolve a per-source descriptor to a delivery mode and
serve bytes for in-browser audition of any supported file.

Modes: native_video / native_audio (HTTP byte-range), cdg (browser canvas renderer),
hls (capped, cached ffmpeg transcode), youtube (iframe), unavailable (+reason).

Nothing here touches the device's primary player or A/V output — it only reads files
and (for exotic video) runs a niced ffmpeg transcode. See
docs/archive/2026-06-30-browser-preview-playback-design.md.
"""
import os


def parse_range(header, size):
    """Parse an HTTP ``Range`` header into inclusive (start, end) byte offsets.

    Returns None for absent/malformed/unsatisfiable ranges (caller then serves the
    whole file or a 416). Only the first range of a multi-range request is honoured.
    """
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        return None
    a, b = spec.split("-", 1)
    try:
        if a == "":                       # suffix range: last N bytes
            n = int(b)
            if n <= 0:
                return None
            return (max(0, size - n), size - 1)
        start = int(a)
        end = int(b) if b != "" else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size:
        return None
    return (start, min(end, size - 1))
