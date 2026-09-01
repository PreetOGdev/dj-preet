import os
import sys
import asyncio
import logging
import uvicorn
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

from bot import bot, player_manager, rearm_bot
from server import app, broadcast_state, setup_player_listener


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DJ-Preet")


async def playback_ticker():
    """Periodically emits playback progress to connected WebSocket clients when playing."""
    while True:
        try:
            player = player_manager.get_primary_player()
            if player.voice_client and player.voice_client.is_playing() and not player.is_paused:
                await broadcast_state(player.get_state())
        except Exception as e:
            pass
        await asyncio.sleep(1.0)


async def run_bot():
    retry_delay = 5
    while True:
        token = os.getenv("DISCORD_TOKEN", "").strip().strip("'\"")
        if not token:
            logger.warning("⚠️ DISCORD_TOKEN is not set in environment or .env!")
            logger.warning("👉 Open the Web Dashboard at http://localhost:8000 to enter your Bot Token.")
            while True:
                await asyncio.sleep(3)
                token = os.getenv("DISCORD_TOKEN", "").strip().strip("'\"")
                if token:
                    break

        try:
            logger.info("🚀 Starting Discord Bot...")
            if bot.is_closed():
                rearm_bot()
            await bot.start(token)
            retry_delay = 5
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "Too Many Requests" in error_str:
                retry_delay = min(120, max(30, retry_delay * 2))
                logger.warning(f"⚠️ Discord 429 Rate Limit encountered. Cooling down for {retry_delay}s to let Discord rate limits clear...")
            else:
                logger.error(f"❌ Discord Bot Error: {e}")
                logger.warning("Check your bot token in .env or on Web Dashboard")
                retry_delay = 10

            await asyncio.sleep(retry_delay)


async def run_web_server():
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    logger.info(f"🌐 Starting Web Dashboard on http://{host}:{port}")

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning"
    )
    server = uvicorn.Server(config)
    while True:
        try:
            await server.serve()
        except Exception as e:
            logger.error(f"❌ Web server error: {e}")
            await asyncio.sleep(2)


async def render_keep_alive():
    """
    Prevents Render Free instances from sleeping after 15 minutes of inactivity
    by sending an inbound HTTP ping to its public URL every 8 minutes.
    """
    await asyncio.sleep(25)  # Wait for web server to start up
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not render_url:
        render_url = os.getenv("APP_URL", "").rstrip("/")
    
    if not render_url:
        logger.info("ℹ️ Local environment detected. Render keep-alive pinger idle.")
        return

    logger.info(f"🔄 Render 24/7 Keep-Alive active for: {render_url}")
    import aiohttp
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{render_url}/api/status", timeout=15) as resp:
                    if resp.status == 200:
                        logger.info("💓 Render Keep-Alive: Pinged public URL (Server staying awake)")
        except Exception as e:
            logger.debug(f"Render keep-alive ping note: {e}")
        await asyncio.sleep(480)  # Ping every 8 minutes (Render sleeps after 15 mins)


async def main():
    setup_player_listener()
    
    # Run bot, web server, periodic ticker, and 24/7 keep-alive pinger concurrently
    await asyncio.gather(
        run_web_server(),
        run_bot(),
        playback_ticker(),
        render_keep_alive(),
        return_exceptions=True
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down DJ-Preet...")
