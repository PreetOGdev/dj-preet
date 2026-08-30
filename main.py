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

from bot import bot, player_manager
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
            await bot.start(token)
        except Exception as e:
            logger.error(f"❌ Discord Bot Error: {e}")
            logger.warning("Check your bot token in .env or on Web Dashboard")
            await asyncio.sleep(5)


async def run_web_server():
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    logger.info(f"🌐 Starting Web Dashboard on http://{host}:{port}")

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        loop="asyncio"
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    setup_player_listener()
    
    # Run bot, web server, and periodic ticker concurrently
    await asyncio.gather(
        run_web_server(),
        run_bot(),
        playback_ticker(),
        return_exceptions=True
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down DJ-Preet...")
