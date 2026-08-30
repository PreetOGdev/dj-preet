# 🎵 DJ-Preet - Discord Music Bot & Real-Time Web Dashboard

An ultra-modern Discord Music Bot paired with a glowing real-time web dashboard. Powered by `discord.py`, `yt-dlp`, and FFmpeg with pre-buffered uncompressed PCM streaming, smart YouTube Autoplay discovery, custom playlist management, and 0ms optimistic UI controls.

---

## ✨ Key Features

- **🌐 Cyber-Violet Web Dashboard**:
  - **Auto Voice Channel Connection**: Automatically detects which voice channel you or your members are in and connects instantly when you add songs.
  - **Smart Autoplay Engine**: Intelligently discovers related, distinct songs by the artist/genre without ever repeating the same track.
  - **"Save to Playlist" System**: Save the current song or your entire active queue to custom persistent playlists backed by SQLite and LocalStorage.
  - **Real-Time DSP Audio Equalizer**: Switch on-the-fly between **Bass Boost**, **Super Bass**, **Nightcore**, **Vaporwave**, **8D Audio**, **Treble Boost**, and **Karaoke**.
  - **Persistent Bottom Player Bar**: Live song titles, artist info, animated equalizer waveform, seekable scrubber, volume slider, and repeat modes.
- **⚡ High-Performance Audio Engine**:
  - `BufferedAudioSource` with 4-second pre-buffering to eliminate voice loss and network dropouts.
  - Zero-latency optimistic UI toggling.
- **🤖 Discord Commands**:
  - Slash commands (`/play`, `/skip`, `/pause`, `/resume`, `/queue`, `/volume`, `/panel`, `/stop`) and prefix commands (`!play`, `!panel`).

---

## 🚀 Local Quick Start (Windows)

1. Double-click **`start.bat`** or run in terminal:
   ```bash
   python main.py
   ```
2. Open **`http://localhost:8000`** in your browser.

---

## ☁️ Deploy to GitHub & Render (24/7 Cloud Hosting)

### 1. Push to GitHub
1. Initialize git and commit:
   ```bash
   git init
   git add .
   git commit -m "Initial commit of DJ-Preet Music Bot"
   ```
2. Create a new repository on [GitHub](https://github.com/new) and push your code:
   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/dj-preet.git
   git branch -M main
   git push -u origin main
   ```
*(Note: `.env` is automatically ignored by `.gitignore` so your Bot Token remains 100% private).*

### 2. Deploy on Render.com
1. Go to [render.com](https://render.com) and create an account.
2. Click **New +** ➔ **Web Service** ➔ connect your `dj-preet` GitHub repository.
3. Configure settings:
   - **Runtime**: `Docker`
   - **Instance Type**: `Free`
4. Under **Environment Variables**, add:
   - `DISCORD_TOKEN`: `your_discord_bot_token`
   - `PORT`: `8000`
   - `HOST`: `0.0.0.0`
5. Click **Create Web Service**. Render will automatically build the container with FFmpeg and give you a live HTTPS URL!

---

## 🔑 Discord Bot Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create an Application named **DJ-Preet** ➔ Go to **Bot** tab:
   - Click **Reset Token** and copy your token.
   - Under **Privileged Gateway Intents**, enable:
     - ✅ **Presence Intent**
     - ✅ **Server Members Intent**
     - ✅ **Message Content Intent**
3. Go to **OAuth2 ➔ URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Connect`, `Speak`, `Use Voice Activity`, `Send Messages`, `Embed Links`, `Read Message History` (or Administrator).
   - Open the generated URL to invite DJ-Preet to your Discord server.
4. Paste your token in the `.env` file (`DISCORD_TOKEN=your_token_here`) or directly in the Web Dashboard Settings modal.

---

## 📁 Project Structure

```
├── audio_source.py       # yt-dlp, FFmpeg audio extraction & Smart Autoplay discovery
├── queue_manager.py      # Guild music player state, queue, loops, and DB sync
├── bot.py                # Discord bot client, voice manager & slash commands
├── server.py             # FastAPI REST API + WebSocket real-time event hub
├── db.py                 # SQLite persistent database (queue, history, playlists)
├── main.py               # Unified concurrent runner for Bot and Web Server
├── start.bat             # 1-click Windows launcher with auto-port cleanup
├── Dockerfile            # Docker container definition with system FFmpeg
├── render.yaml           # Render 1-click blueprint deployment
├── .gitignore            # Git exclusion rules (protects .env token)
├── .dockerignore        # Docker build optimizations
└── static/               # DJ-Preet Web Dashboard Frontend
    ├── index.html        # Main player UI
    ├── css/style.css     # Cyber-violet theme stylesheet
    └── js/app.js         # Real-time WebSocket sync & audio controls
```

