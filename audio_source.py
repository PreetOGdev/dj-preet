"""
audio_source.py — Render-Hardened YouTube Audio Extraction Engine
=================================================================
Built specifically for Render datacenter environments where YouTube
aggressively blocks requests. Every extraction attempt is logged so
failures are always visible in Render logs.
"""

import os
import asyncio
import logging
import random
import re
import tempfile
import time
import traceback
from typing import Optional, List, Dict, Any, Set
import discord
import imageio_ffmpeg
import yt_dlp
import shutil

logger = logging.getLogger("DJ-Preet.Audio")

# ─── FFmpeg Setup ──────────────────────────────────────────────────
system_ffmpeg = shutil.which("ffmpeg")
FFMPEG_EXECUTABLE = system_ffmpeg or imageio_ffmpeg.get_ffmpeg_exe()

# When streaming from a URL we need heavy reconnect flags; when playing
# from a local file those flags cause errors, so we use lighter options.
FFMPEG_STREAM_BEFORE_OPTIONS = (
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
    "-probesize 32k -analyzeduration 0 "
    "-fflags nobuffer+fastseek+discardcorrupt -nostdin"
)
# For local-file playback: simple nostdin is enough.
FFMPEG_FILE_BEFORE_OPTIONS = "-nostdin"
FFMPEG_OPTIONS = "-vn"

# ─── Temp Download Directory ────────────────────────────────────────
# Use /tmp on Linux (Render), or system temp dir on Windows.
TMP_AUDIO_DIR = "/tmp/dj_preet_audio"
try:
    os.makedirs(TMP_AUDIO_DIR, exist_ok=True)
except OSError:
    TMP_AUDIO_DIR = tempfile.gettempdir()

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
# 2026 YouTube Datacenter Strategy:
# - YouTube's SABR experiment blocks direct format URLs on datacenter IPs
# - "The page needs to be reloaded" = consent page, fix: player_skip=webpage
# - "Requested format is not available" = SABR, fix: use non-SABR clients
# - Cookies are REQUIRED on all tiers for datacenter IPs
# - Deno JS runtime is REQUIRED for signature deciphering

def _tier1_opts() -> dict:
    """Tier 1: Default client with session cookies."""
    opts = {
        "format": "ba/b/bestaudio/best",
        "noplaylist": True,
        "nocheckcertificate": True,
        "quiet": True,
        "no_warnings": True,
        "logger": _YTDLLogger(),
        "source_address": "0.0.0.0",
        "extractor_retries": 2,
        # Prevent CPU and network starvation on Render 0.1 vCPU Free Tier
        "ratelimit": 300 * 1024,  # Limit background download to 300 KB/s (still 18x realtime)
        "sleep_interval_requests": 1,  # Brief pause between HTTP requests
    }
    if cookie_file_path and os.path.exists(cookie_file_path):
        opts["cookiefile"] = cookie_file_path
    return opts


def _tier2_opts() -> dict:
    """Tier 2: Android native client."""
    opts = _tier1_opts()
    opts["extractor_args"] = {
        "youtube": {
            "player_client": ["android"]
        }
    }
    return opts


def _tier3_opts() -> dict:
    """Tier 3: iOS and TV client fallback."""
    opts = _tier1_opts()
    opts["extractor_args"] = {
        "youtube": {
            "player_client": ["ios", "tv"]
        }
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

    def _try_extract(tier_name: str, opts: dict) -> dict:
        """Attempt extraction with given options. Returns track dict or None."""
        try:
            logger.info(f"[Extract/{tier_name}] Trying for: {target}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                # Log yt-dlp version on first tier for diagnostics
                if tier_name == "Tier1":
                    logger.info(f"[Extract] yt-dlp version: {yt_dlp.version.__version__}")
                info = ydl.extract_info(target, download=False)
                if info:
                    entry = info["entries"][0] if "entries" in info and info["entries"] else info
                    stream = _get_stream_url(entry)
                    if stream:
                        res = _format_track_entry(entry, target, requester)
                        logger.info(f"[Extract/{tier_name}] ✅ Success: {res['title']}")
                        return res
                    else:
                        # Log format diagnostics when we get info but no URL
                        formats = entry.get("formats", [])
                        logger.warning(
                            f"[Extract/{tier_name}] Got info ({len(formats)} formats) but no stream URL. "
                            f"SABR-only formats likely."
                        )
                        for fmt in formats[:5]:
                            logger.info(
                                f"  itag={fmt.get('format_id')} "
                                f"ext={fmt.get('ext')} "
                                f"acodec={fmt.get('acodec','none')} "
                                f"url={'YES' if fmt.get('url') else 'NO'}"
                            )
        except Exception as e:
            logger.warning(f"[Extract/{tier_name}] Failed: {e}")
        return None

    def _extract():
        tiers = [
            ("Tier1-Default", _tier1_opts()),
            ("Tier2-Android", _tier2_opts()),
            ("Tier3-iOS-TV", _tier3_opts()),
        ]

        for tier_name, opts in tiers:
            result = _try_extract(tier_name, opts)
            if result:
                return result

        logger.error(f"[Extract] ❌ ALL {len(tiers)} TIERS FAILED for: {target}")
        return None

    return await loop.run_in_executor(None, _extract)


# ─── Autoplay Recommendation ─────────────────────────────────────

async def get_recommended_track(seed_track: dict, exclude_ids: Set[str] = None) -> Optional[dict]:
    """Finds a perfectly matching rhythmic song using YouTube's native Radio Mix (RD) algorithm."""
    if not seed_track:
        return None
    if exclude_ids is None:
        exclude_ids = set()

    seed_id = seed_track.get("id")
    webpage_url = seed_track.get("webpage_url", "")
    if not seed_id and "v=" in webpage_url:
        seed_id = webpage_url.split("v=")[1].split("&")[0]

    seen_ids = set(exclude_ids)
    if seed_id:
        seen_ids.add(str(seed_id))

    loop = asyncio.get_running_loop()

    def _fetch_radio_mix():
        if not seed_id:
            return []
        try:
            radio_url = f"https://www.youtube.com/watch?v={seed_id}&list=RD{seed_id}"
            opts = {
                "extract_flat": "in_playlist",
                "skip_download": True,
                "quiet": True,
                "no_warnings": True,
                "logger": _YTDLLogger(),
                "playlistend": 12,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(radio_url, download=False)
                if info:
                    return info.get("entries", [])
        except Exception as e:
            logger.warning(f"[Autoplay] Radio mix fetch note: {e}")
        return []

    entries = await loop.run_in_executor(None, _fetch_radio_mix)

    blacklist = ["jukebox", "full album", "1 hour", "10 hours", "podcast", "mashup", "compilation"]

    for entry in entries:
        if not entry:
            continue
        cand_id = str(entry.get("id"))
        if not cand_id or cand_id in seen_ids:
            continue

        cand_title = entry.get("title", "").lower()
        if any(bad in cand_title for bad in blacklist):
            continue

        duration = entry.get("duration", 0)
        if duration and (duration < 50 or duration > 480):
            continue

        cand_url = f"https://www.youtube.com/watch?v={cand_id}"
        track_info = await extract_audio_info(cand_url, requester="Autoplay ✦")
        if track_info:
            return track_info

    # Fallback to artist search if radio mix had no fresh candidates
    channel = seed_track.get("channel", "")
    if channel and channel != "Unknown Artist":
        candidates = await search_youtube(f"{channel} official audio", limit=5)
        for cand in candidates:
            cand_id = str(cand.get("id"))
            if cand_id and cand_id not in seen_ids:
                return await extract_audio_info(cand.get("url"), requester="Autoplay ✦")

    return None


# ─── Discord Audio Source ─────────────────────────────────────────

class YTDLSource(discord.PCMVolumeTransformer):
    """
    Discord AudioSource that DOWNLOADS the audio to a temp file first,
    then plays from disk. This eliminates CDN throttle, packet loss,
    and voice drops on low-resource environments like Render.
    """

    def __init__(self, source, *, data, volume=0.8, temp_file_path: str = None):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title")
        self.url = data.get("webpage_url")
        self.duration = data.get("duration", 0)
        self._temp_file_path = temp_file_path  # Scheduled for cleanup after playback

    def cleanup(self):
        """Clean up the underlying audio source. Temp file lifecycle is managed by _purge_old_audio_files."""
        try:
            super().cleanup()
        except Exception:
            pass
        # NOTE: We intentionally do NOT delete the temp file here.
        # Files are cleaned up by _purge_old_audio_files() at the start of
        # each new download, so loop-mode tracks stay cached between replays.

    @classmethod
    async def create_source(cls, track_info: dict, *, volume=0.8, filter_name="none", seek_seconds=0):
        """
        Download audio to /tmp and create an FFmpegPCMAudio from the local file.
        Playing from disk is far more stable than streaming from YouTube CDN
        on low-CPU, low-RAM hosts (Render free tier: 0.1 CPU / 512MB RAM).
        """
        loop = asyncio.get_running_loop()
        video_id = track_info.get("id") or "unknown"
        title = track_info.get("title", "Unknown")
        webpage_url = track_info.get("webpage_url") or track_info.get("stream_url") or title

        # ── Step 1: Download the audio to a temp file ──────────────────────
        logger.info(f"[Source] Downloading audio to disk: {title}")

        temp_path = await loop.run_in_executor(None, _download_audio_to_file, track_info)

        if not temp_path:
            # Fallback: stream directly from URL if download failed
            logger.warning(f"[Source] Download failed — falling back to stream URL for: {title}")
            stream_url = track_info.get("stream_url")
            if not stream_url:
                refreshed = await extract_audio_info(webpage_url)
                if refreshed:
                    track_info.update(refreshed)
                    stream_url = track_info.get("stream_url")
            if not stream_url:
                raise ValueError(f"Could not download or stream: {title}")

            before_opts = FFMPEG_STREAM_BEFORE_OPTIONS
            if seek_seconds and seek_seconds > 0:
                before_opts = f"-ss {int(seek_seconds)} {before_opts}"
            filter_opts = AUDIO_FILTERS.get(filter_name, FFMPEG_OPTIONS)
            return cls(
                discord.FFmpegPCMAudio(
                    stream_url,
                    executable=FFMPEG_EXECUTABLE,
                    before_options=before_opts,
                    options=filter_opts,
                ),
                data=track_info, volume=volume, temp_file_path=None
            )

        # ── Step 2: Play from local temp file ─────────────────────────────
        logger.info(f"[Source] ✅ Playing from disk: {temp_path}")

        before_opts = FFMPEG_FILE_BEFORE_OPTIONS
        if seek_seconds and seek_seconds > 0:
            before_opts = f"-ss {int(seek_seconds)} {FFMPEG_FILE_BEFORE_OPTIONS}"

        filter_opts = AUDIO_FILTERS.get(filter_name, FFMPEG_OPTIONS)

        raw_ffmpeg = discord.FFmpegPCMAudio(
            temp_path,
            executable=FFMPEG_EXECUTABLE,
            before_options=before_opts,
            options=filter_opts,
        )
        # No BufferedAudioSource needed — local disk I/O has zero CDN jitter.
        return cls(raw_ffmpeg, data=track_info, volume=volume, temp_file_path=temp_path)


def _purge_old_audio_files(max_age_seconds: float = 300.0):
    """Delete audio files in TMP_AUDIO_DIR older than max_age_seconds.
    
    Called at the start of each new download to keep disk usage in check
    without breaking loop-mode playback (those files stay fresh).
    """
    try:
        now = time.time()
        for fname in os.listdir(TMP_AUDIO_DIR):
            fpath = os.path.join(TMP_AUDIO_DIR, fname)
            try:
                if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > max_age_seconds:
                    os.remove(fpath)
                    logger.debug(f"[TmpClean] Purged old file: {fpath}")
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[TmpClean] Purge scan error: {e}")


def _download_audio_to_file(track_info: dict) -> Optional[str]:
    """
    Download audio from YouTube to a local temp file.
    Returns the path to the downloaded file, or None on failure.

    Strategy:
    - Use the same tier-fallback as extract_audio_info but with download=True
    - Skip FFmpeg post-processing (no codec conversion) — yt-dlp downloads the
      raw opus/m4a/webm stream which FFmpeg can decode natively
    - Target audio-only formats to keep file size small (~5-15 MB per song)
    """
    video_id = track_info.get("id") or "unknown"
    title = track_info.get("title", "Unknown")
    webpage_url = track_info.get("webpage_url") or ""
    stream_url = track_info.get("stream_url") or ""

    if not webpage_url and not stream_url:
        logger.warning(f"[Download] No URL available for: {title}")
        return None

    # Use video ID for filename to allow reuse across seeks/loops
    out_template = os.path.join(TMP_AUDIO_DIR, f"{video_id}.%(ext)s")

    # Purge stale files (>5 min old) to manage Render disk usage before checking cache
    _purge_old_audio_files(max_age_seconds=300.0)

    # Check if already downloaded (e.g. when looping the same track)
    for ext in ["webm", "m4a", "opus", "mp4", "ogg"]:
        cached = os.path.join(TMP_AUDIO_DIR, f"{video_id}.{ext}")
        if os.path.exists(cached) and os.path.getsize(cached) > 10_000:
            logger.info(f"[Download] Cache hit: {cached}")
            return cached


    target = webpage_url or stream_url

    # Build lightweight download options (no post-processing, audio-only)
    def _dl_opts(extra: dict = None) -> dict:
        opts = {
            # Audio-only formats, prefer small ones (opus ~64kbps, m4a ~128kbps)
            # Avoid video formats — we only need audio
            "format": "bestaudio[abr<=128]/bestaudio/ba/b/best",
            "outtmpl": out_template,
            "noplaylist": True,
            "nocheckcertificate": True,
            "quiet": True,
            "no_warnings": True,
            "logger": _YTDLLogger(),
            "source_address": "0.0.0.0",
            # CRITICAL: No post-processors — skip ffmpeg remux entirely
            # The raw stream (opus/m4a/webm) is playable directly by FFmpeg
            "postprocessors": [],
            # Prefer smaller audio streams to save Render disk/RAM
            "prefer_free_formats": True,
        }
        if cookie_file_path and os.path.exists(cookie_file_path):
            opts["cookiefile"] = cookie_file_path
        if extra:
            opts.update(extra)
        return opts

    tiers = [
        ("DL-Tier1", _dl_opts()),
        ("DL-Tier2-Android", _dl_opts({"extractor_args": {"youtube": {"player_client": ["android"]}}})),
        ("DL-Tier3-iOS", _dl_opts({"extractor_args": {"youtube": {"player_client": ["ios", "tv"]}}})),
    ]

    for tier_name, opts in tiers:
        try:
            logger.info(f"[Download/{tier_name}] Downloading: {title} → {out_template}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([target])

            # Find the downloaded file (extension varies by stream)
            for ext in ["webm", "m4a", "opus", "mp4", "ogg", "mp3"]:
                candidate = os.path.join(TMP_AUDIO_DIR, f"{video_id}.{ext}")
                if os.path.exists(candidate) and os.path.getsize(candidate) > 10_000:
                    size_mb = os.path.getsize(candidate) / 1_048_576
                    logger.info(f"[Download/{tier_name}] ✅ Downloaded {size_mb:.1f}MB: {candidate}")
                    return candidate

            logger.warning(f"[Download/{tier_name}] No output file found after download")
        except Exception as e:
            logger.warning(f"[Download/{tier_name}] Failed: {e}")

    logger.error(f"[Download] ❌ All download tiers failed for: {title}")
    return None


async def prefetch_audio_download(track_info: dict) -> Optional[str]:
    """
    Public async wrapper for background pre-downloading the next track's audio.
    Called by queue_manager while current song plays to ensure zero-wait transitions.

    - If the file is already cached on disk (loop mode or rapid re-queue), returns instantly.
    - Runs the download in a thread executor so it doesn't block the event loop.
    - Safe to call multiple times; duplicate calls are deduplicated by the cache check in
      _download_audio_to_file().
    """
    video_id = track_info.get("id") or ""
    title = track_info.get("title", "Unknown")

    if not video_id:
        logger.debug(f"[Prefetch] No video ID for '{title}', skipping pre-download")
        return None

    # Quick cache check without hitting the executor (avoids thread overhead for hits)
    for ext in ["webm", "m4a", "opus", "mp4", "ogg"]:
        cached = os.path.join(TMP_AUDIO_DIR, f"{video_id}.{ext}")
        if os.path.exists(cached) and os.path.getsize(cached) > 10_000:
            logger.info(f"[Prefetch] Already on disk (cache hit): {title}")
            return cached

    logger.info(f"[Prefetch] Pre-downloading in background: {title}")
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _download_audio_to_file, track_info)
        if result:
            logger.info(f"[Prefetch] ✅ Pre-download complete: {title}")
        else:
            logger.warning(f"[Prefetch] Pre-download returned no file for: {title}")
        return result
    except Exception as e:
        logger.warning(f"[Prefetch] Pre-download note for '{title}': {e}")
        return None
