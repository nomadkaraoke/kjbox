"""YouTube search integration — uses yt-dlp to search YouTube for videos."""

import os

import yt_dlp

from utils import log_message

SEARCH_TIMEOUT = 15


def search(query, config=None, max_results=10):
    """Search YouTube for videos matching the query.

    Uses yt-dlp's ytsearch with extract_flat for fast metadata-only results.
    Returns a list of result dicts with id, title, channel, duration, views, and url.
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'skip_download': True,
        'socket_timeout': SEARCH_TIMEOUT,
    }

    cookies_file = (config or {}).get('youtube_cookies_file', '')
    if cookies_file and os.path.exists(cookies_file):
        ydl_opts['cookiefile'] = cookies_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(
                f'ytsearch{max_results}:{query}', download=False
            )
    except Exception as e:
        log_message(f"YouTube search error: {e}", config)
        return []

    if not result or 'entries' not in result:
        return []

    results = []
    for entry in result['entries']:
        if not entry:
            continue
        video_id = entry.get('id', '')
        results.append({
            'id': video_id,
            'title': entry.get('title', ''),
            'channel': entry.get('channel') or entry.get('uploader') or '',
            'duration': entry.get('duration'),
            'duration_str': _format_duration(entry.get('duration')),
            'view_count': entry.get('view_count'),
            'view_count_str': _format_views(entry.get('view_count')),
            'url': f'https://www.youtube.com/watch?v={video_id}',
        })

    return results


def _format_duration(seconds):
    """Format seconds into M:SS or H:MM:SS string."""
    if seconds is None:
        return ''
    total = int(seconds)
    if total < 0:
        return ''
    hours, remainder = divmod(total, 3600)
    mins, secs = divmod(remainder, 60)
    if hours:
        return f'{hours}:{mins:02d}:{secs:02d}'
    return f'{mins}:{secs:02d}'


def _format_views(count):
    """Format view count into a human-readable string like '1.2M' or '45K'."""
    if count is None:
        return ''
    if count >= 1_000_000_000:
        return f'{count / 1_000_000_000:.1f}B'
    if count >= 1_000_000:
        return f'{count / 1_000_000:.1f}M'
    if count >= 1_000:
        return f'{count / 1_000:.1f}K'
    return str(count)
