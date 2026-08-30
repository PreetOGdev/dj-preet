import sqlite3
import json
import os
import time
from typing import List, Dict, Any, Optional

DB_FILE = os.path.join(os.path.dirname(__file__), "flavibot.db")


def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initializes SQLite database tables for FlaviBot."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Guild Settings
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY,
                volume INTEGER DEFAULT 80,
                loop_mode TEXT DEFAULT 'off',
                audio_filter TEXT DEFAULT 'none',
                autoplay_enabled INTEGER DEFAULT 0,
                tracks_played_count INTEGER DEFAULT 0,
                updated_at REAL
            )
        """)

        # 2. Queue Items
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT,
                track_id TEXT,
                title TEXT,
                channel TEXT,
                duration INTEGER,
                formatted_duration TEXT,
                thumbnail TEXT,
                webpage_url TEXT,
                stream_url TEXT,
                requester TEXT,
                position_order INTEGER
            )
        """)

        # 3. History Items
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT,
                track_id TEXT,
                title TEXT,
                channel TEXT,
                duration INTEGER,
                formatted_duration TEXT,
                thumbnail TEXT,
                webpage_url TEXT,
                requester TEXT,
                played_at REAL
            )
        """)

        # 4. Custom Playlists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS custom_playlists (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                tracks_json TEXT NOT NULL,
                created_at REAL
            )
        """)

        conn.commit()


# -------------------------------------------------------------
# Guild Settings CRUD
# -------------------------------------------------------------
def save_guild_settings(guild_id: str, volume: int, loop_mode: str, audio_filter: str, autoplay_enabled: bool, tracks_played_count: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO guild_settings (guild_id, volume, loop_mode, audio_filter, autoplay_enabled, tracks_played_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                volume=excluded.volume,
                loop_mode=excluded.loop_mode,
                audio_filter=excluded.audio_filter,
                autoplay_enabled=excluded.autoplay_enabled,
                tracks_played_count=excluded.tracks_played_count,
                updated_at=excluded.updated_at
        """, (str(guild_id), int(volume), str(loop_mode), str(audio_filter), 1 if autoplay_enabled else 0, int(tracks_played_count), time.time()))
        conn.commit()


def load_guild_settings(guild_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (str(guild_id),))
        row = cursor.fetchone()
        if row:
            return {
                "guild_id": row["guild_id"],
                "volume": row["volume"],
                "loop_mode": row["loop_mode"],
                "audio_filter": row["audio_filter"],
                "autoplay_enabled": bool(row["autoplay_enabled"]),
                "tracks_played_count": row["tracks_played_count"],
            }
        return None


# -------------------------------------------------------------
# Queue Persistence CRUD
# -------------------------------------------------------------
def save_queue(guild_id: str, queue_tracks: List[Dict[str, Any]]):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM queue_items WHERE guild_id = ?", (str(guild_id),))
        for idx, t in enumerate(queue_tracks):
            cursor.execute("""
                INSERT INTO queue_items (guild_id, track_id, title, channel, duration, formatted_duration, thumbnail, webpage_url, stream_url, requester, position_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(guild_id),
                str(t.get("id", "")),
                t.get("title", ""),
                t.get("channel", ""),
                t.get("duration", 0),
                t.get("formatted_duration", ""),
                t.get("thumbnail", ""),
                t.get("webpage_url", ""),
                t.get("stream_url", ""),
                t.get("requester", ""),
                idx
            ))
        conn.commit()


def load_queue(guild_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM queue_items WHERE guild_id = ? ORDER BY position_order ASC", (str(guild_id),))
        rows = cursor.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["track_id"],
                "title": r["title"],
                "channel": r["channel"],
                "duration": r["duration"],
                "formatted_duration": r["formatted_duration"],
                "thumbnail": r["thumbnail"],
                "webpage_url": r["webpage_url"],
                "stream_url": r["stream_url"],
                "requester": r["requester"],
            })
        return result


# -------------------------------------------------------------
# History Persistence CRUD
# -------------------------------------------------------------
def add_history_item(guild_id: str, track_dict: Dict[str, Any]):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO history_items (guild_id, track_id, title, channel, duration, formatted_duration, thumbnail, webpage_url, requester, played_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(guild_id),
            str(track_dict.get("id", "")),
            track_dict.get("title", ""),
            track_dict.get("channel", ""),
            track_dict.get("duration", 0),
            track_dict.get("formatted_duration", ""),
            track_dict.get("thumbnail", ""),
            track_dict.get("webpage_url", ""),
            track_dict.get("requester", "Web Dashboard"),
            time.time()
        ))
        
        # Keep latest 50 history items per guild
        cursor.execute("""
            DELETE FROM history_items WHERE guild_id = ? AND id NOT IN (
                SELECT id FROM history_items WHERE guild_id = ? ORDER BY id DESC LIMIT 50
            )
        """, (str(guild_id), str(guild_id)))
        conn.commit()


def load_history(guild_id: str, limit: int = 40) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM history_items WHERE guild_id = ? ORDER BY id DESC LIMIT ?", (str(guild_id), limit))
        rows = cursor.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["track_id"],
                "title": r["title"],
                "channel": r["channel"],
                "duration": r["duration"],
                "formatted_duration": r["formatted_duration"],
                "thumbnail": r["thumbnail"],
                "webpage_url": r["webpage_url"],
                "requester": r["requester"],
            })
        return result


def clear_history(guild_id: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM history_items WHERE guild_id = ?", (str(guild_id),))
        conn.commit()


# -------------------------------------------------------------
# Custom Playlists CRUD
# -------------------------------------------------------------
def save_custom_playlist(playlist_id: str, name: str, description: str, tracks: List[Dict[str, Any]]):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO custom_playlists (id, name, description, tracks_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                tracks_json=excluded.tracks_json
        """, (str(playlist_id), str(name), str(description), json.dumps(tracks), time.time()))
        conn.commit()


def load_custom_playlists() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM custom_playlists ORDER BY created_at DESC")
        rows = cursor.fetchall()
        result = []
        for r in rows:
            try:
                tracks = json.loads(r["tracks_json"])
            except Exception:
                tracks = []
            result.append({
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "tracks": tracks,
                "created_at": r["created_at"]
            })
        return result


def delete_custom_playlist(playlist_id: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM custom_playlists WHERE id = ?", (str(playlist_id),))
        conn.commit()


# Run initialization on import
init_db()
