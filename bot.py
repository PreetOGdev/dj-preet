import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

from audio_source import extract_audio_info, search_youtube
from queue_manager import MusicPlayerManager, GuildMusicPlayer

load_dotenv()

BOT_PREFIX = os.getenv("BOT_PREFIX", "!")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents, help_command=None)
player_manager = MusicPlayerManager(bot)


def rearm_bot():
    """Re-arms bot client so it can be cleanly started again after an error or close."""
    import aiohttp
    bot._closed = False
    bot.http.connector = aiohttp.TCPConnector(limit=0)
    bot.http._HTTPClient__session = aiohttp.ClientSession(connector=bot.http.connector)


_commands_synced = False

@bot.event
async def on_ready():
    global _commands_synced
    print(f"==================================================")
    print(f"✨ Discord Bot Logged in as: {bot.user} (ID: {bot.user.id})")
    print(f"📡 Connected to {len(bot.guilds)} guilds")
    print(f"🌐 Web Dashboard running on: http://localhost:{os.getenv('PORT', '8000')}")
    print(f"==================================================")

    # Sync slash commands once on startup
    if not _commands_synced:
        try:
            synced = await bot.tree.sync()
            _commands_synced = True
            print(f"✅ Synced {len(synced)} application slash commands.")
        except Exception as e:
            print(f"⚠️ Slash command sync warning: {e}")

    # Set bot rich presence
    activity = discord.Activity(
        type=discord.ActivityType.listening,
        name=f"🎵 /play | DJ-Preet Music"
    )
    await bot.change_presence(activity=activity, status=discord.Status.online)


@bot.event
async def on_voice_state_update(member, before, after):
    """Handle bot being disconnected from voice channel or members leaving."""
    if member == bot.user:
        if before.channel and not after.channel:
            # Bot disconnected
            player = player_manager.get_player(before.channel.guild.id)
            player.voice_client = None
            player.is_paused = False
            player.notify_state_changed()


async def ensure_voice(ctx_or_interaction) -> GuildMusicPlayer:
    """Helper to ensure voice connection and return guild player."""
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    guild = ctx_or_interaction.guild
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author

    if not guild:
        raise commands.CommandError("This command can only be used inside a Discord server.")

    player = player_manager.get_player(guild.id)

    # Check if author is in voice channel
    if not user.voice or not user.voice.channel:
        raise commands.CommandError("You must be in a voice channel to use music commands.")

    target_channel = user.voice.channel
    voice_client = guild.voice_client

    if not voice_client:
        voice_client = await target_channel.connect()
        player.voice_client = voice_client
    elif voice_client.channel != target_channel:
        await voice_client.move_to(target_channel)
        player.voice_client = voice_client
    else:
        player.voice_client = voice_client

    return player


# ---------------------------
# Slash Commands
# ---------------------------

@bot.tree.command(name="play", description="Play a YouTube song or add it to queue")
@app_commands.describe(query="Song title, artist, or YouTube URL")
async def slash_play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    try:
        player = await ensure_voice(interaction)
        track_info = await extract_audio_info(query, requester=interaction.user.display_name)
        if not track_info:
            await interaction.followup.send(f"❌ Could not find track for: `{query}`", ephemeral=True)
            return

        track = player.add_track(track_info)

        embed = discord.Embed(
            title="🎶 Added to Queue",
            description=f"[{track.title}]({track.webpage_url})",
            color=0x8b5cf6
        )
        embed.add_field(name="Duration", value=track.formatted_duration, inline=True)
        embed.add_field(name="Channel", value=track.channel, inline=True)
        embed.add_field(name="Position in Queue", value=f"#{len(player.queue)}", inline=True)
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)
        web_url = os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000"
        embed.set_footer(text=f"Requested by {interaction.user.display_name} • Web: {web_url}")

        await interaction.followup.send(embed=embed)

        # If not playing anything, start playback immediately
        if not player.voice_client.is_playing() and not player.is_paused:
            await player.play_next()

    except Exception as e:
        await interaction.followup.send(f"⚠️ Error: {str(e)}", ephemeral=True)


@bot.tree.command(name="pause", description="Pause current music playback")
async def slash_pause(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild or guild.id not in player_manager.players:
        await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)
        return

    player = player_manager.get_player(guild.id)
    player.pause()
    await interaction.response.send_message("⏸️ Playback paused.")


@bot.tree.command(name="resume", description="Resume music playback")
async def slash_resume(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild or guild.id not in player_manager.players:
        await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)
        return

    player = player_manager.get_player(guild.id)
    player.resume()
    await interaction.response.send_message("▶️ Playback resumed.")


@bot.tree.command(name="skip", description="Skip to the next song in queue")
async def slash_skip(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild or guild.id not in player_manager.players:
        await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)
        return

    player = player_manager.get_player(guild.id)
    skipped = await player.skip()
    if skipped:
        await interaction.response.send_message("⏭️ Skipped current track.")
    else:
        await interaction.response.send_message("❌ No track to skip to.")


@bot.tree.command(name="queue", description="Show the current music queue")
async def slash_queue(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild or guild.id not in player_manager.players:
        await interaction.response.send_message("📭 The queue is currently empty.", ephemeral=True)
        return

    player = player_manager.get_player(guild.id)
    curr = player.current_track
    q = player.queue

    embed = discord.Embed(title=f"📜 Queue for {guild.name}", color=0x8b5cf6)
    if curr:
        embed.add_field(
            name="Now Playing",
            value=f"🎵 **[{curr.title}]({curr.webpage_url})** ({curr.formatted_duration}) - *{curr.requester}*",
            inline=False
        )

    if not q:
        embed.add_field(name="Upcoming Tracks", value="*No upcoming tracks in queue.*", inline=False)
    else:
        lines = []
        for i, t in enumerate(q[:10], start=1):
            lines.append(f"`{i}.` **[{t.title}]({t.webpage_url})** `[{t.formatted_duration}]`")
        if len(q) > 10:
            lines.append(f"*...and {len(q) - 10} more tracks*")
        embed.add_field(name=f"Up Next ({len(q)} tracks)", value="\n".join(lines), inline=False)

    port = os.getenv("PORT", "8000")
    embed.set_footer(text=f"Open Web Player at http://localhost:{port}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="panel", description="Get the direct link to the local Web Player dashboard")
async def slash_panel(interaction: discord.Interaction):
    port = os.getenv("PORT", "8000")
    embed = discord.Embed(
        title="🎛️ DJ-Preet Web Player Dashboard",
        description=f"Control the music player, search songs, manage queue, and adjust equalizers from your browser:\n\n👉 **[Open DJ-Preet Web Player](http://localhost:{port})**",
        color=0x8b5cf6
    )
    embed.set_footer(text="DJ-Preet • Real-time WebSockets Sync")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="volume", description="Set playback volume (1-100%)")
@app_commands.describe(level="Volume percentage from 1 to 100")
async def slash_volume(interaction: discord.Interaction, level: int):
    guild = interaction.guild
    if not guild or guild.id not in player_manager.players:
        await interaction.response.send_message("❌ Bot is not active in voice.", ephemeral=True)
        return

    level = max(1, min(100, level))
    player = player_manager.get_player(guild.id)
    player.set_volume(level)
    await interaction.response.send_message(f"🔊 Volume set to **{level}%**")


@bot.tree.command(name="stop", description="Stop music and clear the queue")
async def slash_stop(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild or guild.id not in player_manager.players:
        await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)
        return

    player = player_manager.get_player(guild.id)
    player.stop()
    if player.voice_client and player.voice_client.is_connected():
        await player.voice_client.disconnect()
        player.voice_client = None
    await interaction.response.send_message("⏹️ Playback stopped and disconnected.")


# ---------------------------
# Prefix Commands (!play, !skip, etc.)
# ---------------------------

@bot.command(name="play", aliases=["p"])
async def cmd_play(ctx, *, query: str):
    try:
        player = await ensure_voice(ctx)
        msg = await ctx.send(f"🔍 Searching YouTube for `{query}`...")
        track_info = await extract_audio_info(query, requester=ctx.author.display_name)
        if not track_info:
            await msg.edit(content=f"❌ Could not find track for: `{query}`")
            return

        track = player.add_track(track_info)
        await msg.edit(content=f"🎶 Added to queue: **{track.title}** `[{track.formatted_duration}]`")

        if not player.voice_client.is_playing() and not player.is_paused:
            await player.play_next()
    except Exception as e:
        await ctx.send(f"⚠️ Error: {str(e)}")


@bot.command(name="skip", aliases=["s", "next"])
async def cmd_skip(ctx):
    guild = ctx.guild
    if guild and guild.id in player_manager.players:
        player = player_manager.get_player(guild.id)
        if await player.skip():
            await ctx.send("⏭️ Skipped.")
        else:
            await ctx.send("❌ No track to skip to.")


@bot.command(name="pause")
async def cmd_pause(ctx):
    guild = ctx.guild
    if guild and guild.id in player_manager.players:
        player = player_manager.get_player(guild.id)
        player.pause()
        await ctx.send("⏸️ Paused.")


@bot.command(name="resume")
async def cmd_resume(ctx):
    guild = ctx.guild
    if guild and guild.id in player_manager.players:
        player = player_manager.get_player(guild.id)
        player.resume()
        await ctx.send("▶️ Resumed.")


@bot.command(name="queue", aliases=["q"])
async def cmd_queue(ctx):
    guild = ctx.guild
    if not guild or guild.id not in player_manager.players:
        await ctx.send("📭 The queue is currently empty.")
        return

    player = player_manager.get_player(guild.id)
    curr = player.current_track
    q = player.queue

    embed = discord.Embed(title=f"📜 Queue for {guild.name}", color=0x8b5cf6)
    if curr:
        embed.add_field(
            name="Now Playing",
            value=f"🎵 **[{curr.title}]({curr.webpage_url})** ({curr.formatted_duration}) - *{curr.requester}*",
            inline=False
        )

    if not q:
        embed.add_field(name="Upcoming Tracks", value="*No upcoming tracks in queue.*", inline=False)
    else:
        lines = [f"`{i}.` **[{t.title}]({t.webpage_url})** `[{t.formatted_duration}]`" for i, t in enumerate(q[:10], start=1)]
        if len(q) > 10:
            lines.append(f"*...and {len(q) - 10} more tracks*")
        embed.add_field(name=f"Up Next ({len(q)} tracks)", value="\n".join(lines), inline=False)

    port = os.getenv("PORT", "8000")
    embed.set_footer(text=f"Web Player: http://localhost:{port}")
    await ctx.send(embed=embed)


@bot.command(name="panel", aliases=["dashboard", "web"])
async def cmd_panel(ctx):
    port = os.getenv("PORT", "8000")
    await ctx.send(f"🎛️ **DJ-Preet Web Dashboard:** http://localhost:{port}")


@bot.command(name="stop", aliases=["leave", "dc"])
async def cmd_stop(ctx):
    guild = ctx.guild
    if guild and guild.id in player_manager.players:
        player = player_manager.get_player(guild.id)
        player.stop()
        if player.voice_client and player.voice_client.is_connected():
            await player.voice_client.disconnect()
            player.voice_client = None
        await ctx.send("⏹️ Disconnected and stopped.")
