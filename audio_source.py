import asyncio
import collections
import io
import random
import re
import threading
import time
import urllib.parse
from typing import Optional, List, Dict, Any, Set
import discord
import imageio_ffmpeg
import yt_dlp

FFMPEG_EXECUTABLE = imageio_ffmpeg.get_ffmpeg_exe()

# Optimized FFmpeg parameters: fast probesize, auto-reconnect on network jitter
FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 10M -analyzeduration 0 -nostdin"
FFMPEG_OPTIONS = "-vn"

# Audio filter presets for Equalizer
AUDIO_FILTERS = {
    "none": "-vn",
    "bassboost": "-vn -af equalizer=f=60:width_type=h:width=50:g=10",
    "superbass": "-vn -af equalizer=f=50:width_type=h:width=40:g=16",
    "nightcore": "-vn -af asetrate=48000*1.22,aresample=48000,atempo=1.05",
    "vaporwave": "-vn -af asetrate=48000*0.82,aresample=48000",
    "8d": "-vn -af apulsator=hz=0.125",
    "treble": "-vn -af equalizer=f=8000:width_type=h:width=1000:g=8",
    "pop": "-vn -af equalizer=f=1000:width_type=h:width=200:g=4,equalizer=f=3000:width_type=h:width=500:g=3",
    "karaoke": "-vn -af pan=stereo|c0=c0-c1|c1=c1-c0",
}

YTDL_OPTIONS = {
    "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
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
            "player_client": ["android", "web"]
        }
    },
}

SEARCH_OPTIONS = {
    "format": "bestaudio/best",
    "extract_flat": True,
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
}

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


async def search_youtube(query: str, limit: int = 8):
    """Searches YouTube for tracks matching query and returns metadata list."""
    loop = asyncio.get_running_loop()

    def _search():
        try:
            if query.startswith("http://") or query.startswith("https://"):
                info = search_ytdl.extract_info(query, download=False)
                if "entries" in info:
                    entries = info["entries"]
                else:
                    entries = [info]
            else:
                info = search_ytdl.extract_info(f"ytsearch{limit}:{query}", download=False)
                entries = info.get("entries", [])

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
    """Extracts direct audio stream URL and detailed track metadata."""
    loop = asyncio.get_running_loop()

    def _extract():
        try:
            info = ytdl.extract_info(query_or_url, download=False)
            if "entries" in info:
                entry = info["entries"][0]
            else:
                entry = info

            if not entry:
                return None

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
                "id": video_id or str(hash(webpage_url)),
                "title": title,
                "channel": channel,
                "duration": duration,
                "formatted_duration": format_duration(duration),
                "thumbnail": thumbnail,
                "webpage_url": webpage_url,
                "stream_url": stream_url,
                "requester": requester,
            }
        except Exception as e:
            print(f"[AudioSource] Extraction error for '{query_or_url}': {e}")
            return None

    return await loop.run_in_executor(None, _extract)


async def get_recommended_track(seed_track: dict, exclude_ids: Set[str] = None) -> Optional[dict]:
    """Finds a distinct, similar recommended YouTube song for Autoplay based on artist, genre, and related music."""
    if not seed_track:
        return None
    if exclude_ids is None:
        exclude_ids = set()

    title = seed_track.get("title", "")
    channel = seed_track.get("channel", "")

    # Clean query from parentheses/remix/feature noise
    clean_title = re.sub(r"[\(\[].*?[\)\]]", "", title)
    clean_title = re.sub(r"(?i)\b(official|video|audio|lyrics|hd|4k|remix|slowed|reverb|version|ft|feat|visualizer)\b", "", clean_title).strip()
    seed_title_words = set(w.lower() for w in re.findall(r"\w+", clean_title) if len(w) > 2)

    # Diverse discovery queries across related artist and mix playlists
    queries = [
        f"{channel} top songs",
        f"songs like {clean_title} {channel}",
        f"{channel} playlist mix",
        f"{clean_title} radio mix"
    ]

    all_candidates = []
    for q in queries:
        try:
            results = await search_youtube(q, limit=8)
            if results:
                all_candidates.extend(results)
        except Exception:
            pass

    seen_ids = set(exclude_ids)
    if seed_track.get("id"):
        seen_ids.add(str(seed_track.get("id")))

    filtered_candidates = []
    for res in all_candidates:
        res_id = str(res.get("id"))
        if not res_id or res_id in seen_ids:
            continue

        res_title = res.get("title", "")
        res_clean = re.sub(r"[\(\[].*?[\)\]]", "", res_title)
        res_clean = re.sub(r"(?i)\b(official|video|audio|lyrics|hd|4k|remix|slowed|reverb|version|ft|feat|visualizer)\b", "", res_clean).strip()
        res_words = set(w.lower() for w in re.findall(r"\w+", res_clean) if len(w) > 2)

        # Skip if title is duplicate/cover upload of the exact same seed song
        if seed_title_words and len(seed_title_words.intersection(res_words)) >= max(1, len(seed_title_words) - 1):
            continue

        seen_ids.add(res_id)
        filtered_candidates.append(res)

    # Pick a random candidate from top 5 distinct results for variety
    if filtered_candidates:
        choice_pool = filtered_candidates[:5]
        selected = random.choice(choice_pool)
        track_info = await extract_audio_info(selected.get("url") or selected.get("title"), requester="Autoplay ✦")
        if track_info:
            return track_info

    # Fallback to broad genre/artist search
    fallback_query = f"{channel} radio" if channel and channel != "Unknown Artist" else "top trending songs"
    try:
        fallback_results = await search_youtube(fallback_query, limit=6)
        for res in fallback_results:
            res_id = str(res.get("id"))
            if res_id not in seen_ids:
                track_info = await extract_audio_info(res.get("url") or res.get("title"), requester="Autoplay ✦")
                if track_info:
                    return track_info
    except Exception:
        pass

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
        # Buffer underrun fallback: return silence frame to avoid Discord dropping voice connection
        return b"\x00" * self.frame_size

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
