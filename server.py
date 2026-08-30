import os
import asyncio
import json
import time
from typing import Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Body, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import set_key, load_dotenv

from bot import bot, player_manager
from audio_source import search_youtube, extract_audio_info, AUDIO_FILTERS
import db

load_dotenv()

app = FastAPI(title="DJ-Preet Web Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connected WebSocket clients
active_websockets: Set[WebSocket] = set()


async def broadcast_state(state: dict = None):
    """Broadcasts player state to all connected web clients."""
    if not active_websockets:
        return
    if state is None:
        player = player_manager.get_primary_player()
        state = player.get_state()

    message = json.dumps({"type": "state_update", "data": state})
    disconnected = set()
    for ws in list(active_websockets):
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        active_websockets.discard(ws)


def get_current_player(guild_id: str = None):
    if guild_id and guild_id.isdigit() and int(guild_id) != 0:
        return player_manager.get_player(int(guild_id))
    return player_manager.get_primary_player()


# Subscribe player updates to broadcast across all guild players
def setup_player_listener():
    def on_state_change(state):
        asyncio.create_task(broadcast_state(state))
    
    player_manager.add_global_listener(on_state_change)


# ---------------------------
# REST API Endpoints
# ---------------------------

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@app.get("/api/status")
async def get_status():
    token = os.getenv("DISCORD_TOKEN", "")
    is_ready = bot.is_ready()
    
    guilds_data = []
    if is_ready:
        for g in bot.guilds:
            voice_channels = [{"id": str(vc.id), "name": vc.name} for vc in g.voice_channels]
            guilds_data.append({
                "id": str(g.id),
                "name": g.name,
                "icon": g.icon.url if g.icon else None,
                "member_count": g.member_count,
                "voice_channels": voice_channels,
            })

    player = player_manager.get_primary_player()
    ping_ms = round(bot.latency * 1000) if is_ready and bot.latency is not None else 0

    return {
        "is_ready": is_ready,
        "token_configured": bool(token and len(token.strip()) > 10),
        "bot_name": bot.user.name if bot.user else "DJ-Preet",
        "bot_avatar": bot.user.avatar.url if bot.user and bot.user.avatar else None,
        "ping_ms": ping_ms,
        "guilds": guilds_data,
        "player_state": player.get_state(),
    }


@app.get("/api/guilds")
async def get_guilds():
    if not bot.is_ready():
        return {"guilds": []}

    result = []
    for g in bot.guilds:
        vcs = [{"id": str(vc.id), "name": vc.name, "user_count": len(vc.members)} for vc in g.voice_channels]
        result.append({
            "id": str(g.id),
            "name": g.name,
            "icon": g.icon.url if g.icon else None,
            "voice_channels": vcs
        })
    return {"guilds": result}


@app.post("/api/voice/join")
async def join_voice(data: dict = Body(...)):
    guild_id = data.get("guild_id")
    channel_id = data.get("channel_id")

    if not bot.is_ready():
        raise HTTPException(status_code=400, detail="Discord bot is not online.")

    guild = bot.get_guild(int(guild_id)) if guild_id else (bot.guilds[0] if bot.guilds else None)
    if not guild:
        raise HTTPException(status_code=404, detail="Discord guild not found.")

    channel = None
    if channel_id:
        channel = guild.get_channel(int(channel_id))
    elif guild.voice_channels:
        channel = guild.voice_channels[0]

    if not channel:
        raise HTTPException(status_code=404, detail="Voice channel not found.")

    player = player_manager.get_player(guild.id)
    voice_client = guild.voice_client

    if not voice_client:
        voice_client = await channel.connect()
    elif voice_client.channel != channel:
        await voice_client.move_to(channel)

    player.voice_client = voice_client
    player.notify_state_changed()
    return {"success": True, "channel_name": channel.name, "guild_name": guild.name}


@app.post("/api/voice/leave")
async def leave_voice(data: dict = Body(default={})):
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)

    if player.voice_client and player.voice_client.is_connected():
        await player.voice_client.disconnect()
        player.voice_client = None
        player.notify_state_changed()
        return {"success": True, "message": "Disconnected from voice."}
    return {"success": False, "message": "Bot is not connected to voice."}


@app.get("/api/search")
async def api_search(q: str = Query(..., min_length=1), limit: int = Query(8, le=20)):
    """Live YouTube track search returning rich preview results."""
    results = await search_youtube(q, limit=limit)
    return {"query": q, "results": results}


@app.get("/api/queue")
async def get_queue(guild_id: str = None):
    player = get_current_player(guild_id)
    return player.get_state()


class AddTrackRequest(BaseModel):
    query: str
    requester: str = "Web Dashboard"
    play_next: bool = False
    guild_id: str = None


async def auto_connect_voice(guild_id: str = None):
    """Automatically finds active user voice channel and connects."""
    if not bot.is_ready() or not bot.guilds:
        return None

    target_guild = None
    if guild_id and str(guild_id).isdigit() and int(guild_id) != 0:
        target_guild = bot.get_guild(int(guild_id))
    if not target_guild and bot.guilds:
        target_guild = bot.guilds[0]

    if not target_guild:
        return None

    player = player_manager.get_player(target_guild.id)
    return await player._ensure_voice_client()


@app.post("/api/queue/add")
async def add_to_queue(req: AddTrackRequest):
    # Auto connect to voice channel if available
    await auto_connect_voice(req.guild_id)
    player = get_current_player(req.guild_id)
    
    # Extract track info from YouTube
    track_info = await extract_audio_info(req.query, requester=req.requester)
    if not track_info:
        # Fallback: Try searching by query string
        results = await search_youtube(req.query, limit=1)
        if results and results[0].get("url"):
            track_info = await extract_audio_info(results[0]["url"], requester=req.requester)

    if not track_info:
        raise HTTPException(status_code=404, detail=f"Could not extract track for: {req.query}")

    track = player.add_track(track_info, play_next=req.play_next)

    # If no track is currently playing, start immediately
    if not player.current_track or (player.voice_client and not player.voice_client.is_playing() and not player.is_paused):
        asyncio.create_task(player.play_next())

    return {"success": True, "track": track.to_dict(), "state": player.get_state()}


@app.post("/api/queue/remove")
async def remove_from_queue(data: dict = Body(...)):
    track_id = str(data.get("id"))
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)

    removed = player.remove_track(track_id)
    if removed:
        return {"success": True, "removed": removed.to_dict(), "state": player.get_state()}
    return {"success": False, "message": "Track not found."}


@app.post("/api/queue/reorder")
async def reorder_queue(data: dict = Body(...)):
    from_idx = int(data.get("from_index", 0))
    to_idx = int(data.get("to_index", 0))
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)

    success = player.move_track(from_idx, to_idx)
    return {"success": success, "state": player.get_state()}


@app.post("/api/queue/clear")
async def clear_queue(data: dict = Body(default={})):
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    player.clear_queue()
    return {"success": True, "state": player.get_state()}


@app.post("/api/queue/shuffle")
async def shuffle_queue(data: dict = Body(default={})):
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    player.shuffle_queue()
    return {"success": True, "state": player.get_state()}


@app.post("/api/queue/move-to-top")
async def move_to_top_queue(data: dict = Body(...)):
    track_id = str(data.get("id"))
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    success = player.move_to_top(track_id)
    return {"success": success, "state": player.get_state()}


@app.get("/api/history")
async def get_history(guild_id: str = None):
    player = get_current_player(guild_id)
    return {
        "history": [t.to_dict() for t in reversed(player.history)],
        "history_count": len(player.history)
    }


@app.post("/api/history/clear")
async def clear_history_api(data: dict = Body(default={})):
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    player.clear_history()
    return {"success": True, "state": player.get_state()}


@app.get("/api/stats")
async def get_stats(guild_id: str = None):
    player = get_current_player(guild_id)
    is_ready = bot.is_ready()
    ping_ms = round(bot.latency * 1000) if is_ready and bot.latency is not None else 0
    return {
        "is_ready": is_ready,
        "ping_ms": ping_ms,
        "guild_count": len(bot.guilds) if is_ready else 0,
        "tracks_played_count": player.tracks_played_count,
        "queue_count": len(player.queue),
        "history_count": len(player.history),
        "uptime_seconds": int(time.time() - player.start_timestamp),
        "is_connected": bool(player.voice_client and player.voice_client.is_connected()),
        "voice_channel_name": player.voice_client.channel.name if player.voice_client and player.voice_client.channel else None,
        "audio_filter": player.audio_filter,
    }


# ---------------------------
# Playback Controls
# ---------------------------

@app.post("/api/playback/play")
async def play_playback(data: dict = Body(default={})):
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)

    if player.is_paused:
        player.resume()
    elif not player.voice_client or not player.voice_client.is_playing():
        await player.play_next()
    return {"success": True, "state": player.get_state()}


@app.post("/api/playback/pause")
async def pause_playback(data: dict = Body(default={})):
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    player.pause()
    return {"success": True, "state": player.get_state()}


@app.post("/api/playback/resume")
async def resume_playback(data: dict = Body(default={})):
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    player.resume()
    return {"success": True, "state": player.get_state()}


@app.post("/api/playback/skip")
async def skip_playback(data: dict = Body(default={})):
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    res = await player.skip()
    return {"success": res, "state": player.get_state()}


@app.post("/api/playback/previous")
async def prev_playback(data: dict = Body(default={})):
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    res = await player.previous()
    return {"success": res, "state": player.get_state()}


@app.post("/api/playback/stop")
async def stop_playback(data: dict = Body(default={})):
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    player.stop()
    return {"success": True, "state": player.get_state()}


@app.post("/api/playback/seek")
async def seek_playback(data: dict = Body(...)):
    seconds = float(data.get("seconds", 0))
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    await player.seek(seconds)
    return {"success": True, "state": player.get_state()}


@app.post("/api/playback/volume")
async def volume_playback(data: dict = Body(...)):
    volume = int(data.get("volume", 80))
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    player.set_volume(volume)
    return {"success": True, "state": player.get_state()}


@app.post("/api/playback/loop")
async def loop_playback(data: dict = Body(...)):
    mode = data.get("mode", "off")
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    player.set_loop_mode(mode)
    return {"success": True, "state": player.get_state()}


@app.post("/api/playback/filter")
async def filter_playback(data: dict = Body(...)):
    filter_name = data.get("filter", "none")
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    await player.set_audio_filter(filter_name)
    return {"success": True, "state": player.get_state()}


@app.post("/api/playback/autoplay")
async def toggle_autoplay(data: dict = Body(...)):
    enabled = data.get("autoplay") if "autoplay" in data else data.get("enabled", False)
    guild_id = data.get("guild_id")
    player = get_current_player(guild_id)
    player.set_autoplay(bool(enabled))
    return {"success": True, "autoplay_enabled": player.autoplay_enabled, "state": player.get_state()}


@app.get("/api/playlists")
async def get_playlists():
    playlists = db.load_custom_playlists()
    return {"playlists": playlists}


@app.post("/api/playlists")
async def save_playlist(data: dict = Body(...)):
    playlist_id = data.get("id") or f"pl_{int(time.time() * 1000)}"
    name = data.get("name", "Untitled Playlist")
    desc = data.get("description", "")
    tracks = data.get("tracks", [])
    db.save_custom_playlist(playlist_id, name, desc, tracks)
    return {"success": True, "playlist": {"id": playlist_id, "name": name, "description": desc, "tracks": tracks}}


@app.post("/api/playlists/delete")
async def delete_playlist(data: dict = Body(...)):
    playlist_id = data.get("id")
    if not playlist_id:
        raise HTTPException(status_code=400, detail="Playlist ID required")
    db.delete_custom_playlist(playlist_id)
    return {"success": True}


@app.get("/api/filters")
async def get_filters():
    return {"available_filters": list(AUDIO_FILTERS.keys())}


# ---------------------------
# Bot Settings & Configuration
# ---------------------------

@app.post("/api/settings/token")
async def update_token(data: dict = Body(...)):
    token = data.get("token", "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token cannot be empty.")

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    set_key(env_path, "DISCORD_TOKEN", token)
    os.environ["DISCORD_TOKEN"] = token

    return {"success": True, "message": "Token saved. Restarting bot client..."}


# ---------------------------
# WebSocket Real-Time Connection
# ---------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)

    player = player_manager.get_primary_player()
    
    # Send initial state snapshot
    try:
        await websocket.send_text(json.dumps({
            "type": "init",
            "data": {
                "bot_status": {
                    "is_ready": bot.is_ready(),
                    "bot_name": bot.user.name if bot.user else "DJ-Preet",
                    "ping_ms": round(bot.latency * 1000) if bot.is_ready() and bot.latency is not None else 0,
                },
                "player_state": player.get_state()
            }
        }))

        while True:
            # Handle incoming WebSocket commands from frontend
            msg_text = await websocket.receive_text()
            msg = json.loads(msg_text)
            action = msg.get("action")

            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "time": time.time()}))
            elif action == "get_state":
                await websocket.send_text(json.dumps({"type": "state_update", "data": player.get_state()}))

    except WebSocketDisconnect:
        active_websockets.discard(websocket)
    except Exception as e:
        active_websockets.discard(websocket)


# Mount frontend static directory
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
