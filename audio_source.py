import os
import asyncio
import collections
import io
import json
import random
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional, List, Dict, Any, Set
import discord
import imageio_ffmpeg
import yt_dlp
import shutil

system_ffmpeg = shutil.which("ffmpeg")
FFMPEG_EXECUTABLE = system_ffmpeg or imageio_ffmpeg.get_ffmpeg_exe()

# Optimized FFmpeg parameters: ultra-fast 32k probesize for instantaneous startup (< 50ms), auto-reconnect on network jitter
FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 32k -analyzeduration 0 -fflags nobuffer+fastseek+discardcorrupt -nostdin"
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

# Cookie file auto-detection (Render Secret Files and Environment Variables)
cookie_file_path = None

# Cookie file auto-detection (Render Secret Files and Environment Variables)
cookie_file_path = None

# 1. Scan Render Secret Files directory (/etc/secrets) for any uploaded cookie file
if os.path.exists("/etc/secrets"):
    for fname in os.listdir("/etc/secrets"):
        fpath = os.path.join("/etc/secrets", fname)
        if os.path.isfile(fpath):
            cookie_file_path = fpath
            print(f"[AudioSource] Loaded Render Secret File: {fpath}")
            break

# 2. Check local workspace directory for cookies.txt
if not cookie_file_path:
    for local_name in ["youtube_cookies.txt", "cookies.txt", "cookie.txt"]:
        local_path = os.path.join(os.path.dirname(__file__), local_name)
        if os.path.exists(local_path):
            cookie_file_path = local_path
            break

# 3. Check for YOUTUBE_COOKIES environment variable and auto-repair Netscape format
raw_cookies = os.getenv("YOUTUBE_COOKIES", "").strip()
if not cookie_file_path and raw_cookies:
    try:
        temp_dir = "/tmp" if os.path.exists("/tmp") else os.path.dirname(__file__)
        cookie_file_path = os.path.join(temp_dir, "youtube_cookies.txt")
        
        # Ensure Netscape header
        formatted_cookies = raw_cookies
        if not formatted_cookies.startswith("# Netscape HTTP Cookie File"):
            formatted_cookies = "# Netscape HTTP Cookie File\n# http://curl.haxx.se/rfc/cookie_spec.html\n" + formatted_cookies
        
        # Ensure proper tab separation for lines
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
    except Exception as e:
        print(f"[AudioSource] Cookie formatting note: {e}")

class SilentYTDLLogger:
    def debug(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        # Suppress redundant YouTube captcha / sign-in warnings since fail-safe handles playback
        if "Sign in to confirm" in msg or "Requested format is not available" in msg or "cookies" in msg:
            return
        if not ("HTTP Error 403" in msg or "429" in msg):
            print(f"[AudioSource/YTDL] {msg}")


YTDL_OPTIONS = {
    "format": "ba/b/bestaudio/best",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "logger": SilentYTDLLogger(),
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web_embedded", "mweb", "ios", "tv"]
        }
    },
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
}

if cookie_file_path and os.path.exists(cookie_file_path):
    YTDL_OPTIONS["cookiefile"] = cookie_file_path

SEARCH_OPTIONS = {
    "format": "ba/b/bestaudio/best",
    "extract_flat": "in_playlist",
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "logger": SilentYTDLLogger(),
    "default_search": "ytsearch",
}

if cookie_file_path and os.path.exists(cookie_file_path):
    SEARCH_OPTIONS["cookiefile"] = cookie_file_path

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
search_ytdl = yt_dlp.YoutubeDL(SEARCH_OPTIONS)


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
    if not entry:
        return ""
    if entry.get("url"):
        return entry.get("url")
    if entry.get("requested_formats"):
        for f in entry["requested_formats"]:
            if f.get("url"):
                return f["url"]
    if entry.get("formats"):
        for f in reversed(entry["formats"]):
            if f.get("url"):
                return f["url"]
    return ""


def _format_track_entry(entry: dict, fallback_query: str, requester: str = "Web User") -> dict:
    video_id = entry.get("id", "")
    title = entry.get("title", "Unknown Title")
    channel = entry.get("uploader") or entry.get("channel") or "Unknown Artist"
    duration = entry.get("duration") or 0
    webpage_url = entry.get("webpage_url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
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


async def search_youtube(query: str, limit: int = 8):
    """Searches YouTube for tracks matching query and returns metadata list."""
    loop = asyncio.get_running_loop()

    def _search():
        try:
            if query.startswith("http://") or query.startswith("https://"):
                info = search_ytdl.extract_info(query, download=False)
                entries = info.get("entries", [info]) if info else []
            else:
                info = search_ytdl.extract_info(f"ytsearch{limit}:{query}", download=False)
                entries = info.get("entries", []) if info else []

            results = []
            for entry in entries:
                if not entry:
                    continue
                video_id = entry.get("id")
                url = f"https://www.youtube.com/watch?v={video_id}" if video_id else (entry.get("url") or "")
                
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
            print(f"[AudioSource] Search error for '{query}': {e}")
            return []

    return await loop.run_in_executor(None, _search)


async def extract_audio_info(query_or_url: str, requester: str = "Web User"):
    """Extracts direct audio stream URL and detailed track metadata with multi-tier fallback."""
    query_or_url = query_or_url.strip()

    # If query is text, resolve top official YouTube video first
    if not (query_or_url.startswith("http://") or query_or_url.startswith("https://")):
        cleaned = re.sub(r"(?i)\b(by|song|songs|track|official|video|audio|lyrics)\b", " ", query_or_url)
        cleaned = " ".join(cleaned.split()) or query_or_url
        yt_candidates = await search_youtube(cleaned, limit=5)
        if not yt_candidates and cleaned != query_or_url:
            yt_candidates = await search_youtube(query_or_url, limit=5)
        if yt_candidates:
            first_target = yt_candidates[0].get("url")
            if first_target:
                query_or_url = first_target

    loop = asyncio.get_running_loop()

    def _extract():
        # 1. Primary extraction with configured options (uses cookies if available)
        try:
            info = ytdl.extract_info(query_or_url, download=False)
            if info:
                entry = info["entries"][0] if "entries" in info and info["entries"] else info
                if entry and _get_stream_url(entry):
                    res = _format_track_entry(entry, query_or_url, requester)
                    print(f"[AudioSource] Extracted successfully: {res.get('title')}")
                    return res
        except Exception:
            pass

        # 2. Pristine Android Client Fallback (Bypasses any expired/broken cookie issues)
        target = query_or_url
        if "youtube.com" in query_or_url or "youtu.be" in query_or_url:
            vid_id = None
            if "v=" in query_or_url:
                vid_id = query_or_url.split("v=")[1].split("&")[0]
            elif "youtu.be/" in query_or_url:
                vid_id = query_or_url.split("youtu.be/")[1].split("?")[0]
            if vid_id:
                target = f"https://www.youtube.com/watch?v={vid_id}"

        try:
            anon_opts = {
                "format": "ba/b/bestaudio/best",
                "noplaylist": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "logger": SilentYTDLLogger(),
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android", "web_embedded", "mweb", "ios", "tv"]
                    }
                },
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
                }
            }
            with yt_dlp.YoutubeDL(anon_opts) as anon_ytdl:
                info = anon_ytdl.extract_info(target, download=False)
                if info:
                    entry = info["entries"][0] if "entries" in info and info["entries"] else info
                    if entry and _get_stream_url(entry):
                        res = _format_track_entry(entry, target, requester)
                        print(f"[AudioSource] Extracted via Android client: {res.get('title')}")
                        return res
        except Exception:
            pass

        # 3. Third pass: try with web_embedded / tv / web clients
        try:
            fallback_opts = {
                "format": "ba/b/bestaudio/best",
                "noplaylist": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "logger": SilentYTDLLogger(),
                "extractor_args": {
                    "youtube": {
                        "player_client": ["web_embedded", "tv", "web"]
                    }
                }
            }
            with yt_dlp.YoutubeDL(fallback_opts) as fb_ytdl:
                info = fb_ytdl.extract_info(target, download=False)
                if info:
                    entry = info["entries"][0] if "entries" in info and info["entries"] else info
                    if entry and _get_stream_url(entry):
                        res = _format_track_entry(entry, target, requester)
                        print(f"[AudioSource] Extracted via TV client: {res.get('title')}")
                        return res
        except Exception:
            pass

        print(f"[AudioSource] Could not extract audio for: {query_or_url}")
        return None

    return await loop.run_in_executor(None, _extract)


async def get_recommended_track(seed_track: dict, exclude_ids: Set[str] = None) -> Optional[dict]:
    """Finds a distinct, similar song matching the exact artist, genre, and album vibe for Autoplay."""
    if not seed_track:
        return None
    if exclude_ids is None:
        exclude_ids = set()

    title = seed_track.get("title", "")
    channel = seed_track.get("channel", "")

    # Clean query from noise
    clean_title = re.sub(r"[\(\[].*?[\)\]]", "", title)
    clean_title = re.sub(r"(?i)\b(official|video|audio|lyrics|hd|4k|remix|slowed|reverb|version|ft|feat|visualizer)\b", "", clean_title).strip()
    seed_title_words = set(w.lower() for w in re.findall(r"\w+", clean_title) if len(w) > 2)

    # Targeted artist and album discovery queries (prevents random genre jumping)
    queries = []
    if channel and channel != "Unknown Artist":
        queries.extend([
            f"{channel} songs official audio",
            f"{channel} top tracks",
            f"{channel} {clean_title} audio"
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
        seen_ids.add(str(seed_track.get("id")))

    # Blacklist keywords that ruin music flow (jukeboxes, 1-hour loops, podcasts)
    blacklist = ["jukebox", "full album", "hour", "hours", "podcast", "mashup", "compilation", "all songs", "live stream"]

    filtered_candidates = []
    for res in all_candidates:
        res_id = str(res.get("id"))
        if not res_id or res_id in seen_ids:
            continue

        duration = res.get("duration", 0)
        # Only accept genuine music tracks between 60s and 450s (7.5 mins)
        if duration and (duration < 60 or duration > 450):
            continue

        res_title = res.get("title", "").lower()
        if any(bad in res_title for bad in blacklist):
            continue

        res_clean = re.sub(r"[\(\[].*?[\)\]]", "", res_title)
        res_clean = re.sub(r"(?i)\b(official|video|audio|lyrics|hd|4k|remix|slowed|reverb|version|ft|feat|visualizer)\b", "", res_clean).strip()
        res_words = set(w.lower() for w in re.findall(r"\w+", res_clean) if len(w) > 2)

        # Skip if title is duplicate/cover upload of the exact same seed song
        if seed_title_words and len(seed_title_words.intersection(res_words)) >= max(1, len(seed_title_words) - 1):
            continue

        seen_ids.add(res_id)
        filtered_candidates.append(res)

    # Pick a candidate from top results for authentic genre continuity
    if filtered_candidates:
        choice_pool = filtered_candidates[:3]
        selected = random.choice(choice_pool)
        track_info = await extract_audio_info(selected.get("url") or selected.get("title"), requester="Autoplay ✦")
        if track_info:
            return track_info

    return None


class BufferedAudioSource(discord.AudioSource):
    """
    Pre-buffered AudioSource that runs a dedicated reader thread holding 3-5 seconds
    of PCM audio chunks ahead of time in a thread-safe ring buffer.
    
    This completely eliminates 1-2 second voice drops and buffering pauses caused
    by YouTube CDN rate-limiting or network packet jitter.
    """

    def __init__(self, raw_source: discord.FFmpegPCMAudio, buffer_seconds: float = 4.0):
        self.raw_source = raw_source
        self.frame_size = 3840  # 20ms of 48kHz 16-bit stereo PCM (discord standard)
        max_chunks = int(buffer_seconds * 50)  # 50 frames = 1 second
        self.buffer = collections.deque(maxlen=max_chunks)
        
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._eof = False
        
        # Start pre-buffering thread
        self._thread = threading.Thread(target=self._buffer_worker, daemon=True)
        self._thread.start()

    def _buffer_worker(self):
        while not self._stopped.is_set():
            # If buffer is full, sleep briefly
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
            except Exception as e:
                self._eof = True
                break

    def read(self) -> bytes:
        with self._lock:
            if self.buffer:
                return self.buffer.popleft()
        if self._eof:
            return b""
        # Seamless zero-lag direct read fallback (avoids inserting silence frame during startup)
        try:
            direct_data = self.raw_source.read()
            if direct_data:
                return direct_data
        except Exception:
            pass
        return b""

    def cleanup(self):
        self._stopped.set()
        if hasattr(self.raw_source, "cleanup"):
            self.raw_source.cleanup()


class YTDLSource(discord.PCMVolumeTransformer):
    """Discord AudioSource created using pre-buffered FFmpeg from raw YouTube audio streams."""

    def __init__(self, source, *, data, volume=0.8):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title")
        self.url = data.get("webpage_url")
        self.duration = data.get("duration", 0)

    @classmethod
    async def create_source(cls, track_info: dict, *, volume=0.8, filter_name="none", seek_seconds=0):
        """Creates a BufferedAudioSource with audio splitting and real-time DSP filter."""
        stream_url = track_info.get("stream_url")
        
        # If stream_url is missing or expired, refresh it
        if not stream_url:
            refreshed = await extract_audio_info(track_info.get("webpage_url") or track_info.get("title"))
            if refreshed:
                track_info.update(refreshed)
                stream_url = track_info.get("stream_url")

        if not stream_url:
            raise ValueError(f"Could not resolve audio stream for track: {track_info.get('title')}")

        before_opts = FFMPEG_BEFORE_OPTIONS
        if seek_seconds and seek_seconds > 0:
            before_opts = f"-ss {int(seek_seconds)} {before_opts}"

        filter_opts = AUDIO_FILTERS.get(filter_name, FFMPEG_OPTIONS)

        raw_ffmpeg_source = discord.FFmpegPCMAudio(
            stream_url,
            executable=FFMPEG_EXECUTABLE,
            before_options=before_opts,
            options=filter_opts,
        )

        buffered_source = BufferedAudioSource(raw_ffmpeg_source, buffer_seconds=4.0)
        return cls(buffered_source, data=track_info, volume=volume)
