"""
audio_source.py — Render-Hardened YouTube Audio Extraction Engine
=================================================================
Built specifically for Render datacenter environments where YouTube
aggressively blocks requests. Every extraction attempt is logged so
failures are always visible in Render logs.
"""

import os
import asyncio
import collections
import io
import json
import logging
import random
import re
import threading
import time
import traceback
import urllib.parse
import urllib.request
from typing import Optional, List, Dict, Any, Set
import discord
import imageio_ffmpeg
import yt_dlp
import shutil

logger = logging.getLogger("DJ-Preet.Audio")

# ─── FFmpeg Setup ──────────────────────────────────────────────────
system_ffmpeg = shutil.which("ffmpeg")
FFMPEG_EXECUTABLE = system_ffmpeg or imageio_ffmpeg.get_ffmpeg_exe()

FFMPEG_BEFORE_OPTIONS = (
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
    "-probesize 32k -analyzeduration 0 "
    "-fflags nobuffer+fastseek+discardcorrupt -nostdin"
)
FFMPEG_OPTIONS = "-vn"

# Audio filter presets for Equalizer
AUDIO_FILTERS = {
    "none": "-vn",
    "bassboost": "-vn -af bass=g=8:f=110:w=0.6",
    "superbass": "-vn -af bass=g=14:f=90:w=0.8",
    "nightcore": "-vn -af asetrate=48000*1.25,aresample=48000,atempo=1.05",
    "vaporwave": "-vn -af asetrate=48000*0.82,aresample=48000,atempo=0.95",
    "8d": "-vn -af apulsator=hz=0.125:amount=1.0",
    "treble": "-vn -af treble=g=7:f=4000:w=0.7",
    "pop": "-vn -af equalizer=f=1000:width_type=h:width=200:g=4,equalizer=f=3000:width_type=h:width=500:g=3",
    "karaoke": "-vn -af pan=stereo|c0=c0-c1|c1=c1-c0",
}

# ─── Cookie File Auto-Detection ───────────────────────────────────
# CRITICAL: yt-dlp needs to READ and WRITE cookies during extraction.
# Render mounts /etc/secrets/ as READ-ONLY, so we must COPY cookie
# files to /tmp/ where yt-dlp can freely update them.
cookie_file_path = None
_writable_cookie_path = "/tmp/yt_cookies.txt"

def _copy_to_writable(src_path: str) -> str:
    """Copy a cookie file to a writable location (/tmp/) for yt-dlp."""
    import shutil as _shutil
    try:
        _shutil.copy2(src_path, _writable_cookie_path)
        logger.info(f"[Cookies] Copied {src_path} → {_writable_cookie_path} (writable)")
        return _writable_cookie_path
    except Exception as e:
        logger.warning(f"[Cookies] Could not copy {src_path} to /tmp/: {e}")
        return src_path  # Fall back to original path

# 1. Render Secret Files directory (/etc/secrets) — READ-ONLY on Render!
if os.path.exists("/etc/secrets"):
    for fname in os.listdir("/etc/secrets"):
        fpath = os.path.join("/etc/secrets", fname)
        if os.path.isfile(fpath):
            logger.info(f"[Cookies] Found Render Secret File: {fpath}")
            cookie_file_path = _copy_to_writable(fpath)
            break

# 2. Local workspace directory
if not cookie_file_path:
    for local_name in ["youtube_cookies.txt", "cookies.txt", "cookie.txt"]:
        local_path = os.path.join(os.path.dirname(__file__), local_name)
        if os.path.exists(local_path):
            cookie_file_path = local_path
            logger.info(f"[Cookies] Loaded local cookie file: {local_path}")
            break

# 3. YOUTUBE_COOKIES environment variable
raw_cookies = os.getenv("YOUTUBE_COOKIES", "").strip()
if not cookie_file_path and raw_cookies:
    try:
        cookie_file_path = _writable_cookie_path

        formatted_cookies = raw_cookies
        if not formatted_cookies.startswith("# Netscape HTTP Cookie File"):
            formatted_cookies = (
                "# Netscape HTTP Cookie File\n"
                "# http://curl.haxx.se/rfc/cookie_spec.html\n"
                + formatted_cookies
            )

        lines = []
        for line in formatted_cookies.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                lines.append(line)
            else:
                parts = re.split(r"[ \t]+", line_str)
                if len(parts) >= 7:
                    lines.append("\t".join(parts[:7]))
                else:
                    lines.append(line)

        with open(cookie_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        logger.info(f"[Cookies] Created cookie file from env var: {cookie_file_path}")
    except Exception as e:
        logger.warning(f"[Cookies] Could not create cookie file from env var: {e}")
        cookie_file_path = None

if cookie_file_path:
    logger.info(f"[Cookies] ✅ Using cookie file: {cookie_file_path}")
else:
    logger.warning("[Cookies] ⚠️ No cookie file found — YouTube may block datacenter requests")


# ─── yt-dlp Logger (suppresses noise, logs real errors) ───────────
class _YTDLLogger:
    """Custom logger that suppresses known-harmless warnings but logs real errors."""
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg):
        # Suppress known sign-in warnings that the fallback chain handles
        if any(s in msg for s in ["Sign in to confirm", "cookies", "HTTP Error 403", "429"]):
            logger.debug(f"[yt-dlp/suppressed] {msg}")
            return
        logger.error(f"[yt-dlp] {msg}")


# ─── yt-dlp Option Profiles ──────────────────────────────────────
# IMPORTANT: On Render datacenter IPs, YouTube's SABR experiment
# often only serves combined video+audio formats (like itag 18/22),
# NOT audio-only streams. We use permissive format strings that
# accept ANY format with audio. FFmpeg's -vn strips the video.

def _base_opts() -> dict:
    """Base options shared across all extraction profiles."""
    opts = {
        "format": "bestaudio/best",  # Accept audio-only OR combined formats
        "noplaylist": True,
        "nocheckcertificate": True,
        "quiet": True,
        "no_warnings": True,
        "logger": _YTDLLogger(),
        "source_address": "0.0.0.0",
    }
    if cookie_file_path and os.path.exists(cookie_file_path):
        opts["cookiefile"] = cookie_file_path
    return opts


def _primary_opts() -> dict:
    """Primary extraction: cookies + default client, permissive format."""
    opts = _base_opts()
    opts["default_search"] = "ytsearch"
    return opts


def _android_opts() -> dict:
    """Android client — datacenter-friendly, accepts ANY format."""
    opts = _base_opts()
    # On SABR-restricted IPs, Android may only serve itag 18 (360p combined)
    opts["format"] = "best"  # Accept literally anything
    opts["extractor_args"] = {
        "youtube": {"player_client": ["android"]}
    }
    opts["http_headers"] = {
        "User-Agent": (
            "com.google.android.youtube/19.29.37 "
            "(Linux; U; Android 14) gzip"
        ),
    }
    return opts


def _web_opts() -> dict:
    """Web client fallback — accepts ANY format."""
    opts = _base_opts()
    opts["format"] = "best"  # Accept literally anything
    opts["extractor_args"] = {
        "youtube": {"player_client": ["web"]}
    }
    opts["http_headers"] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }
    return opts


def _search_opts() -> dict:
    """Lightweight search-only options (extract_flat, no streaming)."""
    opts = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "logger": _YTDLLogger(),
        "default_search": "ytsearch",
    }
    if cookie_file_path and os.path.exists(cookie_file_path):
        opts["cookiefile"] = cookie_file_path
    return opts


# Create persistent instances for search (lightweight, no streaming)
_search_ytdl = yt_dlp.YoutubeDL(_search_opts())


# ─── Helpers ──────────────────────────────────────────────────────

def format_duration(seconds):
    if not seconds or seconds <= 0:
        return "Live / Unknown"
    seconds = int(seconds)
    mins, secs = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def _get_stream_url(entry: dict) -> str:
    """Extract the actual playable stream URL from yt-dlp info dict.
    
    yt-dlp places the URL in different locations depending on the
    client and format selection:
      - entry["url"]              — most common (single format)
      - entry["requested_formats"][0]["url"] — when format merging is needed
      - entry["formats"][-1]["url"]          — last resort from format list
    """
    if not entry:
        return ""
    if entry.get("url"):
        return entry["url"]
    if entry.get("requested_formats"):
        for fmt in entry["requested_formats"]:
            if fmt.get("url"):
                return fmt["url"]
    if entry.get("formats"):
        # Prefer audio-only formats
        for fmt in reversed(entry["formats"]):
            if fmt.get("url") and fmt.get("acodec", "none") != "none":
                return fmt["url"]
        # Fall back to any format with a URL
        for fmt in reversed(entry["formats"]):
            if fmt.get("url"):
                return fmt["url"]
    return ""


def _format_track_entry(entry: dict, fallback_query: str, requester: str = "Web User") -> dict:
    """Normalize yt-dlp info dict into our standard track format."""
    video_id = entry.get("id", "")
    title = entry.get("title", "Unknown Title")
    channel = entry.get("uploader") or entry.get("channel") or "Unknown Artist"
    duration = entry.get("duration") or 0
    webpage_url = entry.get("webpage_url") or (
        f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
    )
    stream_url = _get_stream_url(entry)

    thumbnails = entry.get("thumbnails", [])
    thumbnail = ""
    if thumbnails:
        thumbnail = thumbnails[-1].get("url", "")
    elif video_id:
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    return {
        "id": video_id or str(hash(webpage_url or fallback_query)),
        "title": title,
        "channel": channel,
        "duration": duration,
        "formatted_duration": format_duration(duration),
        "thumbnail": thumbnail,
        "webpage_url": webpage_url,
        "stream_url": stream_url,
        "requester": requester,
    }


def _clean_youtube_url(url: str) -> str:
    """Strip playlist/tracking parameters from YouTube URL to get clean video URL."""
    if "youtube.com" in url or "youtu.be" in url:
        vid_id = None
        if "v=" in url:
            vid_id = url.split("v=")[1].split("&")[0]
        elif "youtu.be/" in url:
            vid_id = url.split("youtu.be/")[1].split("?")[0]
        if vid_id:
            return f"https://www.youtube.com/watch?v={vid_id}"
    return url


# ─── Search ──────────────────────────────────────────────────────

async def search_youtube(query: str, limit: int = 8):
    """Search YouTube and return metadata list (no audio streams)."""
    loop = asyncio.get_running_loop()

    def _search():
        try:
            if query.startswith("http://") or query.startswith("https://"):
                info = _search_ytdl.extract_info(query, download=False)
                entries = info.get("entries", [info]) if info else []
            else:
                info = _search_ytdl.extract_info(f"ytsearch{limit}:{query}", download=False)
                entries = info.get("entries", []) if info else []

            results = []
            for entry in entries:
                if not entry:
                    continue
                video_id = entry.get("id")
                url = (
                    f"https://www.youtube.com/watch?v={video_id}"
                    if video_id
                    else (entry.get("url") or "")
                )
                thumbnails = entry.get("thumbnails", [])
                thumbnail = ""
                if thumbnails:
                    thumbnail = thumbnails[-1].get("url", "")
                elif video_id:
                    thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

                duration = entry.get("duration") or 0
                results.append({
                    "id": video_id or str(hash(url)),
                    "title": entry.get("title") or "Unknown Title",
                    "channel": entry.get("uploader") or entry.get("channel") or "Unknown Artist",
                    "duration": duration,
                    "formatted_duration": format_duration(duration),
                    "thumbnail": thumbnail,
                    "url": url,
                })
            return results
        except Exception as e:
            logger.error(f"[Search] Error for '{query}': {e}")
            return []

    return await loop.run_in_executor(None, _search)


# ─── Audio Extraction (Multi-Tier Fallback) ──────────────────────

async def extract_audio_info(query_or_url: str, requester: str = "Web User"):
    """
    Extract a playable audio stream URL from YouTube.
    
    Uses a 3-tier fallback chain designed for Render datacenter IPs:
      1. Primary (cookies + default client)
      2. Android client (anonymous, no cookies)
      3. Web client (with cookies)
    
    Every attempt is logged so failures are ALWAYS visible in Render logs.
    """
    query_or_url = query_or_url.strip()
    logger.info(f"[Extract] Starting extraction for: {query_or_url}")

    # If input is plain text, resolve to YouTube URL via search first
    if not (query_or_url.startswith("http://") or query_or_url.startswith("https://")):
        cleaned = re.sub(
            r"(?i)\b(by|song|songs|track|official|video|audio|lyrics)\b",
            " ", query_or_url
        )
        cleaned = " ".join(cleaned.split()) or query_or_url

        yt_candidates = await search_youtube(cleaned, limit=5)
        if not yt_candidates and cleaned != query_or_url:
            yt_candidates = await search_youtube(query_or_url, limit=5)

        if yt_candidates:
            first_url = yt_candidates[0].get("url")
            if first_url:
                logger.info(f"[Extract] Resolved text '{query_or_url}' → {first_url}")
                query_or_url = first_url
            else:
                logger.warning(f"[Extract] Search returned results but no URL for: {query_or_url}")
                return None
        else:
            logger.warning(f"[Extract] No search results for: {query_or_url}")
            return None

    # Clean URL to remove playlist/tracking params
    target = _clean_youtube_url(query_or_url)

    loop = asyncio.get_running_loop()

    def _extract():
        # ── Tier 1: Primary extraction (cookies + default client) ──
        try:
            logger.info(f"[Extract/Tier1] Trying primary extraction for: {target}")
            opts = _primary_opts()
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
                if info:
                    entry = info["entries"][0] if "entries" in info and info["entries"] else info
                    stream = _get_stream_url(entry)
                    if stream:
                        res = _format_track_entry(entry, target, requester)
                        logger.info(f"[Extract/Tier1] ✅ Success: {res['title']}")
                        return res
                    else:
                        logger.warning(f"[Extract/Tier1] Got info but no stream URL for: {target}")
        except Exception as e:
            logger.warning(f"[Extract/Tier1] Failed for {target}: {e}")

        # ── Tier 2: Android client (anonymous, datacenter-friendly) ──
        try:
            logger.info(f"[Extract/Tier2] Trying Android client for: {target}")
            opts = _android_opts()
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
                if info:
                    entry = info["entries"][0] if "entries" in info and info["entries"] else info
                    stream = _get_stream_url(entry)
                    if stream:
                        res = _format_track_entry(entry, target, requester)
                        logger.info(f"[Extract/Tier2] ✅ Success (Android): {res['title']}")
                        return res
                    else:
                        logger.warning(f"[Extract/Tier2] Got info but no stream URL for: {target}")
        except Exception as e:
            logger.warning(f"[Extract/Tier2] Failed for {target}: {e}")

        # ── Tier 3: Web client (with cookies) ──
        try:
            logger.info(f"[Extract/Tier3] Trying Web client for: {target}")
            opts = _web_opts()
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
                if info:
                    entry = info["entries"][0] if "entries" in info and info["entries"] else info
                    stream = _get_stream_url(entry)
                    if stream:
                        res = _format_track_entry(entry, target, requester)
                        logger.info(f"[Extract/Tier3] ✅ Success (Web): {res['title']}")
                        return res
                    else:
                        logger.warning(f"[Extract/Tier3] Got info but no stream URL for: {target}")
        except Exception as e:
            logger.warning(f"[Extract/Tier3] Failed for {target}: {e}")

        # ── Tier 4: Last resort — no format filter, dump diagnostics ──
        try:
            logger.info(f"[Extract/Tier4] Last resort (no format filter) for: {target}")
            opts = _base_opts()
            opts["format"] = None  # Accept literally ANY format
            opts["listformats"] = False
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
                if info:
                    entry = info["entries"][0] if "entries" in info and info["entries"] else info
                    # Log available formats for diagnostics
                    formats = entry.get("formats", [])
                    logger.info(f"[Extract/Tier4] Available formats: {len(formats)}")
                    for fmt in formats[:10]:
                        logger.info(
                            f"  itag={fmt.get('format_id')} "
                            f"ext={fmt.get('ext')} "
                            f"acodec={fmt.get('acodec','none')} "
                            f"vcodec={fmt.get('vcodec','none')} "
                            f"url={'YES' if fmt.get('url') else 'NO'}"
                        )
                    stream = _get_stream_url(entry)
                    if stream:
                        res = _format_track_entry(entry, target, requester)
                        logger.info(f"[Extract/Tier4] ✅ Success (last resort): {res['title']}")
                        return res
                    else:
                        logger.error(f"[Extract/Tier4] {len(formats)} formats found but NONE have a playable URL")
        except Exception as e:
            logger.warning(f"[Extract/Tier4] Failed for {target}: {e}")

        logger.error(f"[Extract] ❌ ALL TIERS FAILED for: {target}")
        return None

    return await loop.run_in_executor(None, _extract)


# ─── Autoplay Recommendation ─────────────────────────────────────

async def get_recommended_track(seed_track: dict, exclude_ids: Set[str] = None) -> Optional[dict]:
    """Find a similar song from the same artist/genre for autoplay continuity."""
    if not seed_track:
        return None
    if exclude_ids is None:
        exclude_ids = set()

    title = seed_track.get("title", "")
    channel = seed_track.get("channel", "")

    # Clean noise from title
    clean_title = re.sub(r"[\(\[].*?[\)\]]", "", title)
    clean_title = re.sub(
        r"(?i)\b(official|video|audio|lyrics|hd|4k|remix|slowed|reverb|version|ft|feat|visualizer)\b",
        "", clean_title
    ).strip()
    seed_words = set(w.lower() for w in re.findall(r"\w+", clean_title) if len(w) > 2)

    # Build artist-targeted search queries
    queries = []
    if channel and channel != "Unknown Artist":
        queries.extend([
            f"{channel} songs official audio",
            f"{channel} top tracks",
            f"{channel} {clean_title} audio",
        ])
    else:
        queries.append(f"{clean_title} official audio")

    all_candidates = []
    for q in queries:
        try:
            results = await search_youtube(q, limit=6)
            if results:
                all_candidates.extend(results)
        except Exception:
            pass

    seen_ids = set(exclude_ids)
    if seed_track.get("id"):
        seen_ids.add(str(seed_track["id"]))

    # Blacklist keywords that ruin music flow
    blacklist = [
        "jukebox", "full album", "hour", "hours", "podcast",
        "mashup", "compilation", "all songs", "live stream",
    ]

    filtered = []
    for res in all_candidates:
        res_id = str(res.get("id"))
        if not res_id or res_id in seen_ids:
            continue

        duration = res.get("duration", 0)
        if duration and (duration < 60 or duration > 450):
            continue

        res_title = res.get("title", "").lower()
        if any(bad in res_title for bad in blacklist):
            continue

        # Skip near-duplicate titles
        res_clean = re.sub(r"[\(\[].*?[\)\]]", "", res_title)
        res_clean = re.sub(
            r"(?i)\b(official|video|audio|lyrics|hd|4k|remix|slowed|reverb|version|ft|feat|visualizer)\b",
            "", res_clean
        ).strip()
        res_words = set(w.lower() for w in re.findall(r"\w+", res_clean) if len(w) > 2)

        if seed_words and len(seed_words.intersection(res_words)) >= max(1, len(seed_words) - 1):
            continue

        seen_ids.add(res_id)
        filtered.append(res)

    if filtered:
        choice_pool = filtered[:3]
        selected = random.choice(choice_pool)
        track_info = await extract_audio_info(
            selected.get("url") or selected.get("title"),
            requester="Autoplay ✦",
        )
        if track_info:
            return track_info

    return None


# ─── Buffered Audio Source ────────────────────────────────────────

class BufferedAudioSource(discord.AudioSource):
    """
    Pre-buffered AudioSource with a dedicated reader thread holding
    3-5 seconds of PCM audio ahead of time in a ring buffer.
    Eliminates voice drops from YouTube CDN rate-limiting.
    """

    def __init__(self, raw_source: discord.FFmpegPCMAudio, buffer_seconds: float = 4.0):
        self.raw_source = raw_source
        self.frame_size = 3840  # 20ms of 48kHz 16-bit stereo PCM
        max_chunks = int(buffer_seconds * 50)  # 50 frames per second
        self.buffer = collections.deque(maxlen=max_chunks)
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._eof = False
        self._thread = threading.Thread(target=self._buffer_worker, daemon=True)
        self._thread.start()

    def _buffer_worker(self):
        while not self._stopped.is_set():
            if len(self.buffer) >= self.buffer.maxlen:
                time.sleep(0.015)
                continue
            try:
                data = self.raw_source.read()
                if not data:
                    self._eof = True
                    break
                with self._lock:
                    self.buffer.append(data)
            except Exception:
                self._eof = True
                break

    def read(self) -> bytes:
        with self._lock:
            if self.buffer:
                return self.buffer.popleft()
        if self._eof:
            return b""
        # Direct read fallback during buffer warmup
        try:
            data = self.raw_source.read()
            if data:
                return data
        except Exception:
            pass
        return b""

    def cleanup(self):
        self._stopped.set()
        if hasattr(self.raw_source, "cleanup"):
            self.raw_source.cleanup()


# ─── Discord Audio Source ─────────────────────────────────────────

class YTDLSource(discord.PCMVolumeTransformer):
    """Discord AudioSource created from pre-buffered FFmpeg YouTube audio."""

    def __init__(self, source, *, data, volume=0.8):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title")
        self.url = data.get("webpage_url")
        self.duration = data.get("duration", 0)

    @classmethod
    async def create_source(cls, track_info: dict, *, volume=0.8, filter_name="none", seek_seconds=0):
        """Create a BufferedAudioSource with audio filter and optional seek."""
        stream_url = track_info.get("stream_url")

        # Refresh stream URL if missing or expired
        if not stream_url:
            logger.info(f"[Source] Refreshing stream URL for: {track_info.get('title')}")
            refreshed = await extract_audio_info(
                track_info.get("webpage_url") or track_info.get("title")
            )
            if refreshed:
                track_info.update(refreshed)
                stream_url = track_info.get("stream_url")

        if not stream_url:
            raise ValueError(f"Could not resolve audio stream for: {track_info.get('title')}")

        before_opts = FFMPEG_BEFORE_OPTIONS
        if seek_seconds and seek_seconds > 0:
            before_opts = f"-ss {int(seek_seconds)} {before_opts}"

        filter_opts = AUDIO_FILTERS.get(filter_name, FFMPEG_OPTIONS)

        raw_ffmpeg = discord.FFmpegPCMAudio(
            stream_url,
            executable=FFMPEG_EXECUTABLE,
            before_options=before_opts,
            options=filter_opts,
        )

        buffered = BufferedAudioSource(raw_ffmpeg, buffer_seconds=4.0)
        return cls(buffered, data=track_info, volume=volume)
