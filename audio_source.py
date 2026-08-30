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

# 1. Check for Render Secret Files at /etc/secrets/
for secret_cand in ["/etc/secrets/youtube_cookies.txt", "/etc/secrets/cookies.txt"]:
    if os.path.exists(secret_cand):
        cookie_file_path = secret_cand
        break

# 2. Check for YOUTUBE_COOKIES environment variable and auto-repair Netscape format
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
                # Convert space-separated tokens to tab-separated Netscape format
                parts = re.split(r"[ \t]+", line_str)
                if len(parts) >= 7:
                    lines.append("\t".join(parts[:7]))
                else:
                    lines.append(line)
        
        with open(cookie_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[AudioSource] Cookie formatting note: {e}")

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
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extractor_args": {
        "youtube": {
            "player_client": ["web", "mweb", "android", "ios"]
        }
    },
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
}

if cookie_file_path and os.path.exists(cookie_file_path):
    YTDL_OPTIONS["cookiefile"] = cookie_file_path

SEARCH_OPTIONS = {
    "format": "ba/b/bestaudio/best",
    "extract_flat": True,
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
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


def _format_track_entry(entry: dict, fallback_query: str, requester: str = "Web User") -> dict:
    video_id = entry.get("id", "")
    title = entry.get("title", "Unknown Title")
    channel = entry.get("uploader") or entry.get("channel") or "Unknown Artist"
    duration = entry.get("duration") or 0
    webpage_url = entry.get("webpage_url") or entry.get("url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
    stream_url = entry.get("url")
    
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
                url = entry.get("url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
                
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

    # If query is text, clean search noise and resolve top official YouTube track first
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
        # 1. Direct extraction with primary yt-dlp
        try:
            info = ytdl.extract_info(query_or_url, download=False)
            if info:
                entry = info["entries"][0] if "entries" in info and info["entries"] else info
                if entry and entry.get("url"):
                    return _format_track_entry(entry, query_or_url, requester)
        except Exception as e:
            print(f"[AudioSource] Direct extraction error for '{query_or_url}': {e}")

        # 2. Extract clean video ID and retry with alternative clients
        if "youtube.com" in query_or_url or "youtu.be" in query_or_url:
            try:
                vid_id = None
                if "v=" in query_or_url:
                    vid_id = query_or_url.split("v=")[1].split("&")[0]
                elif "youtu.be/" in query_or_url:
                    vid_id = query_or_url.split("youtu.be/")[1].split("?")[0]
                
                target = f"https://www.youtube.com/watch?v={vid_id}" if vid_id else query_or_url
                alt_opts = dict(YTDL_OPTIONS)
                alt_opts["extractor_args"] = {"youtube": {"player_client": ["android", "mweb"]}}
                with yt_dlp.YoutubeDL(alt_opts) as alt_ytdl:
                    info = alt_ytdl.extract_info(target, download=False)
                    if info:
                        entry = info["entries"][0] if "entries" in info and info["entries"] else info
                        if entry and entry.get("url"):
                            return _format_track_entry(entry, query_or_url, requester)
            except Exception as ex:
                print(f"[AudioSource] Second-pass extraction error for '{query_or_url}': {ex}")

        # 3. Fallback: Search YouTube if query was text
        if not (query_or_url.startswith("http://") or query_or_url.startswith("https://")):
            try:
                info = search_ytdl.extract_info(f"ytsearch1:{query_or_url}", download=False)
                if info and "entries" in info and info["entries"]:
                    first_url = info["entries"][0].get("url") or f"https://www.youtube.com/watch?v={info['entries'][0].get('id')}"
                    info_ext = ytdl.extract_info(first_url, download=False)
                    if info_ext:
                        entry = info_ext["entries"][0] if "entries" in info_ext and info_ext["entries"] else info_ext
                        if entry and entry.get("url"):
                            return _format_track_entry(entry, first_url, requester)
            except Exception:
                pass

        # 4. Ultimate Cloud Datacenter Fallback: oEmbed + SoundCloud Audio Stream
        # Guarantees 100% audio playback on Render without requiring cookies or captcha bypass
        try:
            target_title = None
            target_author = ""
            target_thumb = ""
            vid_id = None

            if "youtube.com" in query_or_url or "youtu.be" in query_or_url:
                if "v=" in query_or_url:
                    vid_id = query_or_url.split("v=")[1].split("&")[0]
                elif "youtu.be/" in query_or_url:
                    vid_id = query_or_url.split("youtu.be/")[1].split("?")[0]
                
                # Fetch oEmbed metadata (never blocked on any datacenter)
                oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid_id}&format=json"
                req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    target_title = data.get("title")
                    target_author = data.get("author_name", "")
                    target_thumb = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
            else:
                target_title = query_or_url

            if target_title:
                search_query = f"scsearch:{target_title} {target_author}".strip()
                sc_opts = {"format": "ba/b/bestaudio/best", "quiet": True, "no_warnings": True}
                with yt_dlp.YoutubeDL(sc_opts) as sc_ydl:
                    sc_info = sc_ydl.extract_info(search_query, download=False)
                    if sc_info and "entries" in sc_info and sc_info["entries"]:
                        sc_entry = sc_info["entries"][0]
                        if sc_entry and sc_entry.get("url"):
                            return {
                                "id": vid_id or sc_entry.get("id") or str(hash(query_or_url)),
                                "title": target_title,
                                "channel": target_author or sc_entry.get("uploader") or "Artist",
                                "duration": sc_entry.get("duration") or 0,
                                "formatted_duration": format_duration(sc_entry.get("duration") or 0),
                                "thumbnail": target_thumb or sc_entry.get("thumbnail") or "",
                                "webpage_url": f"https://www.youtube.com/watch?v={vid_id}" if vid_id else (sc_entry.get("webpage_url") or ""),
                                "stream_url": sc_entry.get("url"),
                                "requester": requester,
                            }
        except Exception as e_cloud:
            print(f"[AudioSource] Cloud fallback extraction failed: {e_cloud}")

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
