import asyncio
import logging
import random
import time
from typing import List, Optional, Dict, Any, Callable
from audio_source import YTDLSource, format_duration, get_recommended_track, prefetch_audio_download
import db

logger = logging.getLogger("DJ-Preet.Queue")


class Track:
    def __init__(self, data: dict):
        self.id = str(data.get("id", ""))
        self.title = data.get("title", "Unknown Title")
        self.channel = data.get("channel", "Unknown Artist")
        self.duration = data.get("duration", 0)
        self.formatted_duration = data.get("formatted_duration") or format_duration(self.duration)
        self.thumbnail = data.get("thumbnail", "")
        self.webpage_url = data.get("webpage_url", "")
        self.stream_url = data.get("stream_url", "")
        self.requester = data.get("requester", "Web Dashboard")
        self.added_at = data.get("added_at") or time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "channel": self.channel,
            "duration": self.duration,
            "formatted_duration": self.formatted_duration,
            "thumbnail": self.thumbnail,
            "webpage_url": self.webpage_url,
            "stream_url": self.stream_url,
            "requester": self.requester,
            "added_at": self.added_at,
        }


class GuildMusicPlayer:
    """Manages audio queue, playback status, volume, filters, database persistence, autoplay, and voice streaming."""

    def __init__(self, guild_id: int, bot_client):
        self.guild_id = guild_id
        self.bot = bot_client
        self.queue: List[Track] = []
        self.history: List[Track] = []
        self.current_track: Optional[Track] = None
        self.voice_client = None

        self.volume: float = 0.8
        self.loop_mode: str = "off"  # "off", "track", "queue"
        self.audio_filter: str = "none"
        self.autoplay_enabled: bool = False

        self.is_paused: bool = False
        self.is_loading: bool = False

        # Playback timing tracking
        self.track_start_time: float = 0
        self.pause_start_time: float = 0
        self.total_paused_duration: float = 0
        self.seek_offset: float = 0

        # Path of the previous track's temp audio file — deleted when next song starts
        self._prev_track_file_path: Optional[str] = None
        # Guard to prevent duplicate concurrent autoplay prefetch tasks
        self._autoplay_prefetch_running: bool = False

        self.tracks_played_count: int = 0
        self.start_timestamp: float = time.time()
        self._state_listeners: List[Callable[[dict], Any]] = []
        self._playback_lock = asyncio.Lock()
        self._manual_stop: bool = False

        # Load persisted settings, queue, and history from SQLite Database
        self._load_from_db()

    def _load_from_db(self):
        try:
            settings = db.load_guild_settings(str(self.guild_id))
            if settings:
                self.volume = max(0.0, min(2.0, settings.get("volume", 80) / 100.0))
                self.loop_mode = settings.get("loop_mode", "off")
                self.audio_filter = settings.get("audio_filter", "none")
                self.autoplay_enabled = settings.get("autoplay_enabled", False)
                self.tracks_played_count = settings.get("tracks_played_count", 0)

            saved_queue = db.load_queue(str(self.guild_id))
            self.queue = [Track(t) for t in saved_queue]

            saved_history = db.load_history(str(self.guild_id))
            self.history = [Track(t) for t in reversed(saved_history)]
        except Exception as e:
            print(f"[Player Error] Failed to load DB state for guild {self.guild_id}: {e}")

    def _save_settings_to_db(self):
        try:
            db.save_guild_settings(
                guild_id=str(self.guild_id),
                volume=int(self.volume * 100),
                loop_mode=self.loop_mode,
                audio_filter=self.audio_filter,
                autoplay_enabled=self.autoplay_enabled,
                tracks_played_count=self.tracks_played_count
            )
        except Exception as e:
            print(f"[Player Error] DB save settings error: {e}")

    def _save_queue_to_db(self):
        try:
            db.save_queue(str(self.guild_id), [t.to_dict() for t in self.queue])
        except Exception as e:
            print(f"[Player Error] DB save queue error: {e}")

    def add_state_listener(self, callback: Callable[[dict], Any]):
        if callback not in self._state_listeners:
            self._state_listeners.append(callback)

    def remove_state_listener(self, callback: Callable[[dict], Any]):
        if callback in self._state_listeners:
            self._state_listeners.remove(callback)

    def notify_state_changed(self):
        state = self.get_state()
        for cb in self._state_listeners:
            try:
                res = cb(state)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as e:
                print(f"[Player Error] Listener callback error: {e}")

    def get_position(self) -> float:
        """Returns current playback position in seconds."""
        if not self.current_track or not self.voice_client or not self.voice_client.is_playing():
            if self.is_paused and self.current_track:
                elapsed = self.pause_start_time - self.track_start_time - self.total_paused_duration + self.seek_offset
                return max(0.0, min(float(self.current_track.duration), elapsed))
            return 0.0

        elapsed = time.time() - self.track_start_time - self.total_paused_duration + self.seek_offset
        if self.current_track.duration > 0:
            return max(0.0, min(float(self.current_track.duration), elapsed))
        return max(0.0, elapsed)

    def get_state(self) -> dict:
        pos = self.get_position()
        curr_dict = self.current_track.to_dict() if self.current_track else None
        
        voice_channel_name = None
        voice_channel_id = None
        is_connected = False
        if self.voice_client and self.voice_client.is_connected():
            is_connected = True
            if self.voice_client.channel:
                voice_channel_name = self.voice_client.channel.name
                voice_channel_id = str(self.voice_client.channel.id)

        return {
            "guild_id": str(self.guild_id),
            "is_connected": is_connected,
            "voice_channel_name": voice_channel_name,
            "voice_channel_id": voice_channel_id,
            "is_playing": bool((self.voice_client and self.voice_client.is_playing()) or (self.current_track is not None and not self.is_paused and not self.is_loading)),
            "is_paused": self.is_paused,
            "is_loading": self.is_loading,
            "current_track": curr_dict,
            "position": round(pos, 1),
            "formatted_position": format_duration(pos),
            "queue": [t.to_dict() for t in self.queue],
            "queue_count": len(self.queue),
            "volume": int(self.volume * 100),
            "loop_mode": self.loop_mode,
            "audio_filter": self.audio_filter,
            "autoplay_enabled": self.autoplay_enabled,
            "history": [t.to_dict() for t in reversed(self.history[-30:])],
            "history_count": len(self.history),
            "tracks_played_count": self.tracks_played_count,
            "uptime_seconds": int(time.time() - self.start_timestamp),
        }

    def add_track(self, track_info: dict, play_next: bool = False) -> Track:
        track = Track(track_info)
        if play_next:
            self.queue.insert(0, track)
        else:
            self.queue.append(track)
        self._save_queue_to_db()
        self.notify_state_changed()
        return track

    def remove_track(self, track_id: str) -> Optional[Track]:
        for i, t in enumerate(self.queue):
            if t.id == track_id or str(i) == track_id:
                removed = self.queue.pop(i)
                self._save_queue_to_db()
                self.notify_state_changed()
                return removed
        return None

    def move_track(self, from_idx: int, to_idx: int) -> bool:
        if 0 <= from_idx < len(self.queue) and 0 <= to_idx < len(self.queue):
            track = self.queue.pop(from_idx)
            self.queue.insert(to_idx, track)
            self._save_queue_to_db()
            self.notify_state_changed()
            return True
        return False

    def move_to_top(self, track_id: str) -> bool:
        for i, t in enumerate(self.queue):
            if t.id == track_id or str(i) == track_id:
                track = self.queue.pop(i)
                self.queue.insert(0, track)
                self._save_queue_to_db()
                self.notify_state_changed()
                return True
        return False

    def clear_queue(self):
        self.queue.clear()
        self._save_queue_to_db()
        self.notify_state_changed()

    def clear_history(self):
        self.history.clear()
        db.clear_history(str(self.guild_id))
        self.notify_state_changed()

    def shuffle_queue(self):
        if len(self.queue) > 1:
            random.shuffle(self.queue)
            self._save_queue_to_db()
            self.notify_state_changed()

    def set_loop_mode(self, mode: str):
        if mode in ["off", "track", "queue"]:
            self.loop_mode = mode
            self._save_settings_to_db()
            self.notify_state_changed()

    def set_autoplay(self, enabled: bool):
        self.autoplay_enabled = bool(enabled)
        if self.autoplay_enabled and self.loop_mode == "track":
            self.loop_mode = "off"
        self._save_settings_to_db()
        self.notify_state_changed()
        if self.autoplay_enabled and len(self.queue) == 0 and self.current_track:
            asyncio.create_task(self._prefetch_next_autoplay_track())

    def set_volume(self, volume_percent: int):
        self.volume = max(0.0, min(2.0, volume_percent / 100.0))
        if self.voice_client and hasattr(self.voice_client, "source") and self.voice_client.source:
            if hasattr(self.voice_client.source, "volume"):
                self.voice_client.source.volume = self.volume
        self._save_settings_to_db()
        self.notify_state_changed()

    async def set_audio_filter(self, filter_name: str):
        self.audio_filter = filter_name
        self._save_settings_to_db()
        self.notify_state_changed()
        if self.current_track and self.voice_client and (self.voice_client.is_playing() or self.is_paused):
            cur_pos = self.get_position()
            await self._start_playback(self.current_track, seek_seconds=cur_pos)

    async def seek(self, target_seconds: float):
        if not self.current_track:
            return
        target_seconds = max(0.0, min(float(self.current_track.duration or 99999), float(target_seconds)))
        self.seek_offset = target_seconds
        self.track_start_time = time.time()
        self.pause_start_time = 0
        self.total_paused_duration = 0
        self.notify_state_changed()

        if self.voice_client and self.voice_client.is_connected():
            await self._start_playback(self.current_track, seek_seconds=target_seconds)

    def pause(self):
        if self.voice_client and self.voice_client.is_playing() and not self.is_paused:
            self.voice_client.pause()
            self.is_paused = True
            self.pause_start_time = time.time()
            self.notify_state_changed()

    def resume(self):
        if self.voice_client and self.is_paused:
            self.voice_client.resume()
            self.is_paused = False
            if self.pause_start_time > 0:
                self.total_paused_duration += time.time() - self.pause_start_time
                self.pause_start_time = 0
            self.notify_state_changed()

    async def skip(self) -> bool:
        if not self.voice_client:
            return False
        if self.loop_mode == "track":
            self.loop_mode = "off"

        if self.voice_client.is_playing() or self.is_paused:
            self.voice_client.stop()
            return True
        elif self.queue or self.autoplay_enabled:
            await self.play_next()
            return True
        return False

    async def previous(self) -> bool:
        if not self.history:
            return False
        prev_track = self.history.pop()
        if self.current_track:
            self.queue.insert(0, self.current_track)
            self._save_queue_to_db()
        await self._start_playback(prev_track)
        return True

    def stop(self):
        self.queue.clear()
        self._save_queue_to_db()
        self.current_track = None
        self.is_paused = False
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.voice_client.stop()
        self.notify_state_changed()

    async def _ensure_voice_client(self):
        """Auto-connects to active user voice channel if not already connected."""
        if self.voice_client and self.voice_client.is_connected():
            return self.voice_client
        if not self.bot.is_ready() or not self.bot.guilds:
            return None

        guild = None
        if self.guild_id and self.guild_id != 0:
            guild = self.bot.get_guild(self.guild_id)
        if not guild and self.bot.guilds:
            guild = self.bot.guilds[0]

        if not guild:
            return None

        # Priority 1: Channel with active non-bot members
        target_vc = None
        for vc in guild.voice_channels:
            if [m for m in vc.members if not m.bot]:
                target_vc = vc
                break
        # Priority 2: Channel with music/general in name
        if not target_vc:
            for vc in guild.voice_channels:
                if any(k in vc.name.lower() for k in ["music", "song", "listen", "general"]):
                    target_vc = vc
                    break
        # Priority 3: First voice channel
        if not target_vc and guild.voice_channels:
            target_vc = guild.voice_channels[0]

        if self.voice_client and self.voice_client.is_connected():
            return self.voice_client

        if target_vc:
            try:
                if not guild.voice_client:
                    self.voice_client = await target_vc.connect(timeout=10.0, reconnect=True)
                elif not guild.voice_client.is_connected():
                    try:
                        await guild.voice_client.disconnect(force=True)
                    except Exception:
                        pass
                    self.voice_client = await target_vc.connect(timeout=10.0, reconnect=True)
                else:
                    self.voice_client = guild.voice_client
                    if [m for m in target_vc.members if not m.bot] and self.voice_client.channel != target_vc:
                        await self.voice_client.move_to(target_vc)
                self.notify_state_changed()
                return self.voice_client
            except Exception as e:
                print(f"[Player Error] Auto voice connect error: {e}")
        return None

    async def play_next(self):
        async with self._playback_lock:
            # Auto connect to voice if not connected
            await self._ensure_voice_client()

            last_played_track = self.current_track

            # Handle looping modes (Autoplay overrides single-track loop)
            if self.loop_mode == "track" and not self.autoplay_enabled and self.current_track:
                next_track = self.current_track
            elif self.loop_mode == "queue" and self.current_track:
                self.queue.append(self.current_track)
                self._save_queue_to_db()
                next_track = self.queue.pop(0) if self.queue else None
            else:
                if self.current_track:
                    self.history.append(self.current_track)
                    db.add_history_item(str(self.guild_id), self.current_track.to_dict())
                    if len(self.history) > 50:
                        self.history.pop(0)
                next_track = self.queue.pop(0) if self.queue else None
                self._save_queue_to_db()

            # Autoplay: queue is still empty after song ends.
            # The background prefetch SHOULD have already added a track.
            # If it hasn't (e.g. bot just enabled, or prefetch failed), wait briefly
            # before doing a fresh fetch — but don't block the event loop for 10+ seconds.
            if not next_track and self.autoplay_enabled:
                seed = last_played_track or (self.history[-1] if self.history else None)
                if seed and not self._autoplay_prefetch_running:
                    # Give the background prefetch one last chance (it may be mid-flight)
                    for _ in range(6):  # Wait up to 3s (6 × 0.5s)
                        await asyncio.sleep(0.5)
                        if self.queue:
                            next_track = self.queue.pop(0)
                            self._save_queue_to_db()
                            break

                # Still nothing — do a fresh fetch as last resort
                if not next_track and seed:
                    self.is_loading = True
                    self.notify_state_changed()
                    exclude_ids = {t.id for t in self.history}
                    if seed.id:
                        exclude_ids.add(seed.id)
                    rec_info = await get_recommended_track(seed.to_dict(), exclude_ids=exclude_ids)
                    if rec_info:
                        next_track = Track(rec_info)

            if not next_track:
                self.current_track = None
                self.is_paused = False
                self.notify_state_changed()
                return

            self.current_track = next_track
            self.notify_state_changed()

            if self.voice_client and self.voice_client.is_connected():
                await self._start_playback(next_track)
            else:
                self.is_loading = False
                self.notify_state_changed()

    async def _start_playback(self, track: Track, seek_seconds: float = 0):
        self.is_loading = True
        self.notify_state_changed()

        try:
            source = await YTDLSource.create_source(
                track.to_dict(),
                volume=self.volume,
                filter_name=self.audio_filter,
                seek_seconds=seek_seconds,
            )

            def after_playback(error):
                if self._manual_stop:
                    # Ignore stop triggered during manual seek or track switch
                    self._manual_stop = False
                    return
                if error:
                    logger.warning(f"[Player] Audio playback finished with error: {error}")
                fut = asyncio.run_coroutine_threadsafe(self._handle_track_ended(), self.bot.loop)
                try:
                    fut.result(timeout=5)
                except Exception as ex:
                    logger.warning(f"[Player] after_playback exception: {ex}")

            if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
                self._manual_stop = True
                self.voice_client.stop()

            # ── Delete previous song's temp file, but ONLY if it's a different file ──
            # Get the new source's file path FIRST before any deletion.
            new_file_path = getattr(source, "_temp_file_path", None)

            # Only delete if it's a different file — same path = loop mode (same song),
            # deleting it would yank the file out from under the new FFmpeg process.
            if self._prev_track_file_path and self._prev_track_file_path != new_file_path:
                import os
                try:
                    if os.path.exists(self._prev_track_file_path):
                        os.remove(self._prev_track_file_path)
                        logger.debug(f"[TmpClean] Deleted previous track file: {self._prev_track_file_path}")
                except Exception as e:
                    logger.debug(f"[TmpClean] Could not delete previous track file: {e}")

            # Track this song's file for deletion when the next song starts
            self._prev_track_file_path = new_file_path

            self.current_track = track
            self.is_paused = False
            self.is_loading = False
            self.track_start_time = time.time()
            self.pause_start_time = 0
            self.total_paused_duration = 0
            self.seek_offset = float(seek_seconds)

            if self.voice_client:
                self.voice_client.play(source, after=after_playback)
            self.tracks_played_count += 1
            self._save_settings_to_db()
            self.notify_state_changed()

            # ── Background pipeline: discover + pre-download next track ──────────
            # Both tasks run in parallel. Prefetch adds a track to the queue;
            # predownload polls the queue and downloads as soon as one appears.
            if self.autoplay_enabled and len(self.queue) == 0:
                asyncio.create_task(self._prefetch_next_autoplay_track())
            # Always launch predownload — it will poll queue and handle both
            # manual-queue tracks (immediately available) and autoplay tracks
            # (added ~10-15s later by _prefetch_next_autoplay_track).
            asyncio.create_task(self._predownload_next_track())

        except Exception as e:
            logger.error(f"[Player] Failed to play track '{track.title}': {e}")
            self.is_loading = False
            self.notify_state_changed()
            if self.queue or self.autoplay_enabled:
                await self.play_next()

    async def _predownload_next_track(self, delay_seconds: float = 0):
        """
        Downloads the next track's audio file to disk while the current song plays.
        Polls the queue for up to 60s so it catches autoplay tracks even if the
        radio-mix fetch takes a while (typically 10-15s on Render).

        Pipeline:
          Song A plays → _predownload_next_track() runs in background
            → waits for queue to have a track (polls every 2s, up to 60s)
            → Song B downloaded to /tmp/B.webm
          Song A ends → cache hit → Song B plays instantly
        """
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        # Poll until something is in the queue or timeout
        waited = 0
        poll_interval = 2.0
        max_wait = 60.0
        while not self.queue:
            if waited >= max_wait:
                logger.debug("[Predownload] No track appeared in queue within 60s, giving up")
                return
            await asyncio.sleep(poll_interval)
            waited += poll_interval

        next_track = self.queue[0]  # Peek (don't pop)
        logger.info(f"[Predownload] Starting background download: {next_track.title}")
        try:
            await prefetch_audio_download(next_track.to_dict())
            logger.info(f"[Predownload] ✅ Ready on disk: {next_track.title}")
        except Exception as e:
            logger.warning(f"[Predownload] Note for '{next_track.title}': {e}")

    async def _prefetch_next_autoplay_track(self):
        """Discovers next autoplay song metadata and adds it to queue, then triggers audio pre-download."""
        if not self.autoplay_enabled or len(self.queue) > 0 or not self.current_track:
            return
        if self._autoplay_prefetch_running:
            logger.debug("[Autoplay] Prefetch already running, skipping duplicate")
            return
        self._autoplay_prefetch_running = True
        try:
            exclude_ids = {t.id for t in self.history}
            if self.current_track.id:
                exclude_ids.add(self.current_track.id)
            for q in self.queue:
                if q.id:
                    exclude_ids.add(q.id)

            rec_info = await get_recommended_track(self.current_track.to_dict(), exclude_ids=exclude_ids)
            if rec_info and len(self.queue) == 0 and self.autoplay_enabled:
                prefetched_track = Track(rec_info)
                self.queue.append(prefetched_track)
                self._save_queue_to_db()
                self.notify_state_changed()
                logger.info(f"[Autoplay] ✅ Pre-fetched next track: {prefetched_track.title}")
                # _predownload_next_track is already polling — it will pick this up automatically
        except Exception as e:
            logger.warning(f"[Autoplay] Pre-fetch note: {e}")
        finally:
            self._autoplay_prefetch_running = False

    async def _handle_track_ended(self):
        await self.play_next()


class MusicPlayerManager:
    """Manages all GuildMusicPlayers across guilds."""

    def __init__(self, bot_client):
        self.bot = bot_client
        self.players: Dict[int, GuildMusicPlayer] = {}
        self.default_player_id: int = 0
        self._global_listeners: List[Callable[[dict], Any]] = []

    def add_global_listener(self, callback: Callable[[dict], Any]):
        if callback not in self._global_listeners:
            self._global_listeners.append(callback)
            for p in self.players.values():
                p.add_state_listener(callback)

    def get_player(self, guild_id: int) -> GuildMusicPlayer:
        if guild_id not in self.players:
            player = GuildMusicPlayer(guild_id, self.bot)
            for cb in self._global_listeners:
                player.add_state_listener(cb)
            self.players[guild_id] = player
        return self.players[guild_id]

    def get_primary_player(self) -> GuildMusicPlayer:
        """Returns the active guild player or primary server player for single-guild/localhost use."""
        for player in self.players.values():
            if player.voice_client and player.voice_client.is_connected():
                return player
        if self.bot and self.bot.guilds:
            return self.get_player(self.bot.guilds[0].id)
        if self.players:
            return next(iter(self.players.values()))
        return self.get_player(0)
