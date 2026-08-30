/**
 * DJ-Preet Music Player - Core Client Application
 * Fully responsive, optimistic real-time UI with instant button toggling,
 * auto voice channel detection, and comprehensive "Save to Playlist" system.
 */

const API_BASE = window.location.origin;
let ws = null;
let currentPlaybackState = null;
let searchDebounceTimer = null;
let isSeeking = false;
let positionTicker = null;
let lastVolumeBeforeMute = 80;

// State for Save to Playlist Modal
let currentSaveTargetMode = "current"; // "current", "queue", or "specific"
let specificTrackToSave = null;

// Curated Preset Playlist Tracks
const PRESET_PLAYLISTS = {
  lofi: [
    "lofi hip hop radio beats to relax study to",
    "chillhop essentials lofi beats",
    "cozy night lofi chill beats",
    "tokyo night vibes lofi hip hop",
    "coffee shop ambient lofi study"
  ],
  synthwave: [
    "synthwave radio chill retro drive",
    "kavinsky nightcall",
    "the midnight sunset synthwave",
    "home resonance synthwave",
    "cyberpunk synthwave dark electro"
  ],
  bass: [
    "phonk gym playlist bass boosted",
    "trap bangers heavy bass mix",
    "skrillex bangarang",
    "cyberpunk dark club bass boost",
    "montagem coral phonk bass boosted"
  ],
  hits: [
    "top hits 2026 global chart songs",
    "the weeknd blinding lights",
    "dua lipa levitating",
    "post malone sunflower",
    "harry styles as it was"
  ],
  acoustic: [
    "acoustic guitar relaxing instrumental",
    "coffeehouse acoustic covers playlist",
    "peaceful piano melodies instrumental",
    "indie folk acoustic morning coffee",
    "fingerstyle guitar relaxing music"
  ]
};

// ==========================================
// Initialization
// ==========================================
document.addEventListener("DOMContentLoaded", () => {
  initWebSocket();
  initEventListeners();
  initSearch();
  initNavigation();
  initPlaylists();
  initAudioFx();
  initCommandsCheatsheet();
  loadBotStatus();
  startLocalProgressTicker();
});

// Helper to reliably render Lucide icons in any element
function setButtonIcon(el, iconName) {
  if (!el) return;
  el.innerHTML = `<i data-lucide="${iconName}"></i>`;
  lucide.createIcons({ roots: [el] });
}

// ==========================================
// WebSocket Real-Time Connection
// ==========================================
function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log("🟢 WebSocket Connected to DJ-Preet Server");
  };

  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === "init") {
        updateBotStatus(payload.data.bot_status);
        updatePlayerState(payload.data.player_state);
      } else if (payload.type === "state_update") {
        updatePlayerState(payload.data);
      }
    } catch (e) {
      console.error("WS Parse Error:", e);
    }
  };

  ws.onclose = () => {
    console.warn("🔴 WebSocket Disconnected. Reconnecting in 2s...");
    setTimeout(initWebSocket, 2000);
  };

  ws.onerror = (err) => {
    console.error("WS Error:", err);
  };
}

// ==========================================
// REST API Helper (Auto-updates state on response)
// ==========================================
async function apiCall(endpoint, method = "GET", body = null) {
  try {
    const options = {
      method,
      headers: { "Content-Type": "application/json" }
    };
    if (body) options.body = JSON.stringify(body);

    const res = await fetch(`${API_BASE}${endpoint}`, options);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "API Request Failed");
    }
    const data = await res.json();
    
    // Automatically apply returned state immediately
    if (data && data.state) {
      updatePlayerState(data.state);
    }
    return data;
  } catch (err) {
    console.error(`API Error on [${method}] ${endpoint}:`, err);
    showToast(err.message, "error");
    throw err;
  }
}

// ==========================================
// State Updating & UI Rendering
// ==========================================
function updatePlayerState(state) {
  if (!state) return;
  currentPlaybackState = state;

  // 1. Voice Connection Indicator
  const voiceDot = document.getElementById("botStatusDot");
  const voiceLabel = document.getElementById("voiceChannelLabel");
  const sidebarVoiceDot = document.getElementById("sidebarVoiceDot");
  const sidebarVoiceTitle = document.getElementById("sidebarVoiceTitle");
  const sidebarVoiceDesc = document.getElementById("sidebarVoiceDesc");
  const sidebarVoiceBtn = document.getElementById("sidebarVoiceActionBtn");

  if (state.is_connected && state.voice_channel_name) {
    voiceDot.className = "status-dot connected";
    voiceLabel.textContent = state.voice_channel_name;
    sidebarVoiceDot.className = "voice-card-dot connected";
    sidebarVoiceTitle.textContent = "Voice Connected";
    sidebarVoiceDesc.textContent = `Streaming in: 🔊 #${state.voice_channel_name}`;
    sidebarVoiceBtn.innerHTML = `<i data-lucide="phone-off"></i> <span>Disconnect</span>`;
  } else {
    voiceDot.className = "status-dot disconnected";
    voiceLabel.textContent = "Not in Voice";
    sidebarVoiceDot.className = "voice-card-dot";
    sidebarVoiceTitle.textContent = "Voice Disconnected";
    sidebarVoiceDesc.textContent = "DJ-Preet connects automatically when you add songs!";
    sidebarVoiceBtn.innerHTML = `<i data-lucide="phone-call"></i> <span>Connect Voice</span>`;
  }
  lucide.createIcons({ roots: [document.getElementById("sidebarVoiceCard")] });

  // 2. Currently Playing Track (Hero Card & Bottom Bar)
  const curr = state.current_track;
  const heroArtwork = document.getElementById("heroArtwork");
  const heroTrackTitle = document.getElementById("heroTrackTitle");
  const heroTrackArtist = document.getElementById("heroTrackArtist");
  const heroTrackRequester = document.getElementById("heroTrackRequester");
  const heroEqBars = document.getElementById("heroEqBars");
  const heroFilterBadge = document.getElementById("heroFilterBadge");
  const heroStatusBadge = document.getElementById("heroStatusBadge");

  const bottomThumb = document.getElementById("currentTrackThumb");
  const bottomTitle = document.getElementById("currentTrackTitle");
  const bottomArtist = document.getElementById("currentTrackArtist");
  const bottomRequester = document.getElementById("currentTrackRequester");
  const bottomWave = document.getElementById("trackWaveAnim");

  const isPlaying = state.is_playing && !state.is_paused;

  if (curr) {
    const defaultThumb = "https://images.unsplash.com/photo-1614613535308-eb5fbd3d2c17?w=300&auto=format&fit=crop&q=80";
    const artworkSrc = curr.thumbnail || defaultThumb;

    heroArtwork.src = artworkSrc;
    heroTrackTitle.textContent = curr.title;
    heroTrackArtist.textContent = curr.channel;
    heroTrackRequester.innerHTML = `<i data-lucide="user"></i> ${curr.requester || "Web Dashboard"}`;

    bottomThumb.src = artworkSrc;
    bottomTitle.textContent = curr.title;
    bottomArtist.textContent = curr.channel;
    bottomRequester.textContent = curr.requester || "Web Dashboard";

    document.title = `${isPlaying ? "▶ " : "⏸ "}${curr.title} - DJ-Preet`;
  } else {
    const fallbackThumb = "https://images.unsplash.com/photo-1614613535308-eb5fbd3d2c17?w=300&auto=format&fit=crop&q=80";
    heroArtwork.src = fallbackThumb;
    heroTrackTitle.textContent = "No Song Playing";
    heroTrackArtist.textContent = "Search a song above or click a quick starter to play!";
    heroTrackRequester.innerHTML = `<i data-lucide="user"></i> Web Dashboard`;

    bottomThumb.src = fallbackThumb;
    bottomTitle.textContent = "No Song Playing";
    bottomArtist.textContent = "DJ-Preet Discord Player";
    bottomRequester.textContent = "Web Dashboard";

    document.title = "DJ-Preet - Discord Music Player";
  }

  // Active equalizer waves & status badge
  if (isPlaying) {
    heroEqBars.style.display = "flex";
    bottomWave.style.display = "flex";
    heroStatusBadge.innerHTML = `<i data-lucide="disc"></i> NOW PLAYING`;
  } else if (state.is_paused) {
    heroEqBars.style.display = "none";
    bottomWave.style.display = "none";
    heroStatusBadge.innerHTML = `<i data-lucide="pause-circle"></i> PAUSED`;
  } else if (state.is_loading) {
    heroEqBars.style.display = "none";
    bottomWave.style.display = "none";
    heroStatusBadge.innerHTML = `<i data-lucide="loader"></i> LOADING AUDIO`;
  } else {
    heroEqBars.style.display = "none";
    bottomWave.style.display = "none";
    heroStatusBadge.innerHTML = `<i data-lucide="disc"></i> READY`;
  }
  lucide.createIcons({ roots: [heroStatusBadge, heroTrackRequester] });

  // 3. Play/Pause Button Icon
  const playPauseBtn = document.getElementById("playPauseBtn");
  setButtonIcon(playPauseBtn, isPlaying ? "pause" : "play");

  // 4. Loop Mode
  const loopBtn = document.getElementById("loopBtn");
  if (state.loop_mode === "track") {
    loopBtn.classList.add("active");
    loopBtn.title = "Loop Mode: Current Track (Click for Queue Loop)";
    setButtonIcon(loopBtn, "repeat-1");
  } else if (state.loop_mode === "queue") {
    loopBtn.classList.add("active");
    loopBtn.title = "Loop Mode: Entire Queue (Click to Turn Off)";
    setButtonIcon(loopBtn, "repeat");
  } else {
    loopBtn.classList.remove("active");
    loopBtn.title = "Loop Mode: Off (Click to Turn On)";
    setButtonIcon(loopBtn, "repeat");
  }

  // 4b. Autoplay State Sync
  const autoplayToggleBtn = document.getElementById("autoplayToggleBtn");
  const bottomAutoplayBtn = document.getElementById("bottomAutoplayBtn");
  const autoplayPill = document.getElementById("autoplayPill");

  if (state.autoplay_enabled) {
    if (autoplayToggleBtn) {
      autoplayToggleBtn.classList.add("active");
      autoplayToggleBtn.title = "Autoplay is ON (Click to disable)";
    }
    if (bottomAutoplayBtn) {
      bottomAutoplayBtn.classList.add("active");
      bottomAutoplayBtn.title = "Autoplay: ON (Continuous smart playback)";
    }
    if (autoplayPill) {
      autoplayPill.textContent = "ON";
    }
  } else {
    if (autoplayToggleBtn) {
      autoplayToggleBtn.classList.remove("active");
      autoplayToggleBtn.title = "Autoplay is OFF (Click to enable)";
    }
    if (bottomAutoplayBtn) {
      bottomAutoplayBtn.classList.remove("active");
      bottomAutoplayBtn.title = "Autoplay: OFF (Click to enable)";
    }
    if (autoplayPill) {
      autoplayPill.textContent = "OFF";
    }
  }

  // 5. Audio FX Filter Indicator
  const filterDot = document.getElementById("filterDot");
  const sidebarFxBadge = document.getElementById("sidebarFxBadge");
  const activeFilterName = document.getElementById("activeFilterName");

  const filterName = state.audio_filter || "none";
  if (filterName !== "none") {
    filterDot.style.display = "block";
    sidebarFxBadge.style.display = "inline-block";
    heroFilterBadge.style.display = "inline-block";
    heroFilterBadge.textContent = filterName.toUpperCase();
    if (activeFilterName) activeFilterName.textContent = formatFilterName(filterName);
  } else {
    filterDot.style.display = "none";
    sidebarFxBadge.style.display = "none";
    heroFilterBadge.style.display = "none";
    if (activeFilterName) activeFilterName.textContent = "Normal / Flat";
  }

  // Update Audio FX cards active highlight
  document.querySelectorAll(".fx-card").forEach((card) => {
    card.classList.toggle("active", card.getAttribute("data-filter") === filterName);
  });

  // 6. Volume Slider & Mute Icon
  const volumeRange = document.getElementById("volumeRange");
  const volumePercent = document.getElementById("volumePercent");
  const volumeMuteBtn = document.getElementById("volumeMuteBtn");

  if (document.activeElement !== volumeRange) {
    volumeRange.value = state.volume;
    volumePercent.textContent = `${state.volume}%`;
  }

  const volIconName = state.volume === 0 ? "volume-x" : (state.volume < 50 ? "volume-1" : "volume-2");
  setButtonIcon(volumeMuteBtn, volIconName);

  // 7. Update Scrubber & Time Displays
  updateScrubber(state.position, curr ? curr.duration : 0);

  // 8. Render Queue & History
  renderQueue(state.queue);
  if (state.history) renderHistory(state.history);
}

function formatFilterName(filter) {
  const map = {
    none: "Normal / Studio Flat",
    bassboost: "Bass Boost 🔊",
    superbass: "Super Bass 💥",
    nightcore: "Nightcore 🌙",
    vaporwave: "Vaporwave 📼",
    "8d": "8D Surround 🎧",
    treble: "Treble Boost ✨",
    pop: "Pop Acoustic 🎵",
    karaoke: "Karaoke 🎤"
  };
  return map[filter] || filter.toUpperCase();
}

function updateScrubber(position, duration) {
  if (isSeeking) return;

  const currentStamp = document.getElementById("currentTimeStamp");
  const totalStamp = document.getElementById("totalDurationStamp");
  const fill = document.getElementById("seekSliderFill");
  const handle = document.getElementById("seekSliderHandle");
  const rangeInput = document.getElementById("seekRangeInput");

  const heroCurrentTime = document.getElementById("heroCurrentTime");
  const heroTotalDuration = document.getElementById("heroTotalDuration");
  const heroProgressFill = document.getElementById("heroProgressFill");

  const pos = Math.max(0, position || 0);
  const dur = Math.max(0, duration || 0);

  const formattedPos = formatTime(pos);
  const formattedDur = formatTime(dur);

  currentStamp.textContent = formattedPos;
  totalStamp.textContent = formattedDur;
  if (heroCurrentTime) heroCurrentTime.textContent = formattedPos;
  if (heroTotalDuration) heroTotalDuration.textContent = formattedDur;

  const percent = dur > 0 ? Math.min(100, (pos / dur) * 100) : 0;
  fill.style.width = `${percent}%`;
  handle.style.left = `${percent}%`;
  rangeInput.value = percent;
  if (heroProgressFill) heroProgressFill.style.width = `${percent}%`;
}

function startLocalProgressTicker() {
  if (positionTicker) clearInterval(positionTicker);
  positionTicker = setInterval(() => {
    if (currentPlaybackState && currentPlaybackState.is_playing && !currentPlaybackState.is_paused && !isSeeking) {
      currentPlaybackState.position += 1;
      const dur = currentPlaybackState.current_track ? currentPlaybackState.current_track.duration : 0;
      if (dur > 0 && currentPlaybackState.position > dur) {
        currentPlaybackState.position = dur;
      }
      updateScrubber(currentPlaybackState.position, dur);
    }
  }, 1000);
}

function formatTime(seconds) {
  if (!seconds || seconds <= 0 || isNaN(seconds)) return "00:00";
  seconds = Math.floor(seconds);
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

// ==========================================
// Queue Rendering & Actions
// ==========================================
function renderQueue(queue) {
  const container = document.getElementById("queueItemsList");
  const queueTracksBadge = document.getElementById("queueTracksBadge");
  const sidebarQueueCount = document.getElementById("sidebarQueueCount");

  const trackCount = queue ? queue.length : 0;
  sidebarQueueCount.textContent = trackCount;

  // Calculate total queue duration
  let totalSeconds = 0;
  if (queue) {
    queue.forEach((t) => (totalSeconds += t.duration || 0));
  }
  queueTracksBadge.textContent = `${trackCount} track${trackCount === 1 ? "" : "s"} • ${formatTime(totalSeconds)} total`;

  if (!queue || queue.length === 0) {
    container.innerHTML = `
      <div class="empty-state-card">
        <div class="empty-state-icon"><i data-lucide="music-2"></i></div>
        <h3 class="empty-state-title">Your queue is empty</h3>
        <p class="empty-state-desc">Search songs above, paste a YouTube link, or click a quick starter to get the music playing!</p>
        <div class="empty-quick-starters">
          <button class="quick-starter-pill" onclick="queuePreset('lofi')"><i data-lucide="coffee"></i> Lofi Chill</button>
          <button class="quick-starter-pill" onclick="queuePreset('synthwave')"><i data-lucide="sunset"></i> Synthwave</button>
          <button class="quick-starter-pill" onclick="queuePreset('bass')"><i data-lucide="zap"></i> Bass Workout</button>
          <button class="quick-starter-pill" onclick="queuePreset('hits')"><i data-lucide="flame"></i> Top Chart Hits</button>
        </div>
      </div>
    `;
    lucide.createIcons({ roots: [container] });
    return;
  }

  let html = "";
  queue.forEach((track, index) => {
    const thumb = track.thumbnail || "https://images.unsplash.com/photo-1614613535308-eb5fbd3d2c17?w=100&auto=format&fit=crop&q=80";
    const duration = track.formatted_duration || formatTime(track.duration);
    const requester = track.requester || "Web Dashboard";
    const encodedTrack = encodeURIComponent(JSON.stringify(track));

    html += `
      <div class="queue-item" data-index="${index}" data-id="${track.id}">
        <span class="queue-index">${index + 1}</span>
        <img src="${thumb}" alt="${track.title}" class="queue-thumb" onerror="this.src='https://images.unsplash.com/photo-1614613535308-eb5fbd3d2c17?w=100&auto=format&fit=crop&q=80'">
        <div class="queue-info">
          <div class="queue-title" title="${track.title}">${track.title}</div>
          <div class="queue-meta">
            <span class="queue-channel">${track.channel}</span>
            <span>•</span>
            <span class="queue-duration">${duration}</span>
            <span>•</span>
            <span class="queue-requester-badge"><i data-lucide="user"></i> ${requester}</span>
          </div>
        </div>
        <div class="queue-actions">
          ${index > 0 ? `
            <button class="queue-icon-btn" onclick="moveQueueTrack(${index}, ${index - 1})" title="Move Up">
              <i data-lucide="chevron-up"></i>
            </button>
          ` : ""}
          ${index < queue.length - 1 ? `
            <button class="queue-icon-btn" onclick="moveQueueTrack(${index}, ${index + 1})" title="Move Down">
              <i data-lucide="chevron-down"></i>
            </button>
          ` : ""}
          <button class="queue-icon-btn" onclick="openSaveModalForTrack('${encodedTrack}')" title="Save this song to playlist">
            <i data-lucide="bookmark-plus"></i>
          </button>
          <button class="queue-icon-btn" onclick="moveToTopQueue('${track.id}')" title="Play Next (Move to Top)">
            <i data-lucide="list-plus"></i>
          </button>
          <button class="queue-icon-btn remove" onclick="removeQueueTrack('${track.id}')" title="Remove track">
            <i data-lucide="trash-2"></i>
          </button>
        </div>
      </div>
    `;
  });

  container.innerHTML = html;
  lucide.createIcons({ roots: [container] });
}

// Instant optimistic queue actions
async function removeQueueTrack(trackId) {
  if (currentPlaybackState && currentPlaybackState.queue) {
    currentPlaybackState.queue = currentPlaybackState.queue.filter((t) => t.id !== trackId);
    renderQueue(currentPlaybackState.queue);
  }
  await apiCall("/api/queue/remove", "POST", { id: trackId });
  showToast("Track removed from queue", "info");
}

async function moveToTopQueue(trackId) {
  if (currentPlaybackState && currentPlaybackState.queue) {
    const idx = currentPlaybackState.queue.findIndex((t) => t.id === trackId);
    if (idx > -1) {
      const item = currentPlaybackState.queue.splice(idx, 1)[0];
      currentPlaybackState.queue.unshift(item);
      renderQueue(currentPlaybackState.queue);
    }
  }
  await apiCall("/api/queue/move-to-top", "POST", { id: trackId });
  showToast("Track moved to play next!", "success");
}

async function moveQueueTrack(fromIdx, toIdx) {
  if (currentPlaybackState && currentPlaybackState.queue) {
    const item = currentPlaybackState.queue.splice(fromIdx, 1)[0];
    currentPlaybackState.queue.splice(toIdx, 0, item);
    renderQueue(currentPlaybackState.queue);
  }
  await apiCall("/api/queue/reorder", "POST", { from_index: fromIdx, to_index: toIdx });
}

// ==========================================
// YouTube Universal Search
// ==========================================
function initSearch() {
  const searchInput = document.getElementById("globalSearchInput");
  const searchDropdown = document.getElementById("searchResultsDropdown");
  const searchResultsList = document.getElementById("searchResultsList");
  const clearBtn = document.getElementById("searchClearBtn");
  const spinner = document.getElementById("searchSpinner");

  // Shortcut key '/'
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== searchInput && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
      e.preventDefault();
      searchInput.focus();
    }
  });

  searchInput.addEventListener("input", (e) => {
    const val = searchInput.value.trim();
    clearBtn.style.display = val ? "flex" : "none";

    if (!val) {
      searchDropdown.style.display = "none";
      return;
    }

    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(async () => {
      searchDropdown.style.display = "block";
      spinner.style.display = "inline-flex";
      searchResultsList.innerHTML = `<div style="padding: 16px; text-align: center; color: var(--text-muted);">Searching YouTube...</div>`;

      try {
        const data = await apiCall(`/api/search?q=${encodeURIComponent(val)}&limit=8`);
        spinner.style.display = "none";
        renderSearchResults(data.results);
      } catch (err) {
        spinner.style.display = "none";
        searchResultsList.innerHTML = `<div style="padding: 16px; text-align: center; color: var(--accent-danger);">Search error: ${err.message}</div>`;
      }
    }, 300);
  });

  // Direct Enter to queue if search or URL
  searchInput.addEventListener("keydown", async (e) => {
    if (e.key === "Enter") {
      const val = searchInput.value.trim();
      if (!val) return;
      searchDropdown.style.display = "none";
      searchInput.value = "";
      clearBtn.style.display = "none";
      showToast(`Adding "${val}" to queue...`, "info");
      try {
        await apiCall("/api/queue/add", "POST", { query: val });
        showToast("Added song to queue!", "success");
      } catch (err) {
        showToast("Failed to add song", "error");
      }
    }
  });

  clearBtn.addEventListener("click", () => {
    searchInput.value = "";
    clearBtn.style.display = "none";
    searchDropdown.style.display = "none";
  });

  // Close dropdown on click outside
  document.addEventListener("click", (e) => {
    if (!document.querySelector(".global-search-container").contains(e.target)) {
      searchDropdown.style.display = "none";
    }
  });
}

function renderSearchResults(results) {
  const container = document.getElementById("searchResultsList");
  if (!results || results.length === 0) {
    container.innerHTML = `<div style="padding: 16px; text-align: center; color: var(--text-muted);">No YouTube tracks found.</div>`;
    return;
  }

  let html = "";
  results.forEach((item) => {
    const thumb = item.thumbnail || "https://images.unsplash.com/photo-1614613535308-eb5fbd3d2c17?w=100&auto=format&fit=crop&q=80";
    const queryPayload = encodeURIComponent(item.url || item.title);

    html += `
      <div class="search-result-item" onclick="queueTrackFromSearch('${queryPayload}')">
        <img src="${thumb}" alt="${item.title}" class="search-item-thumb" onerror="this.src='https://images.unsplash.com/photo-1614613535308-eb5fbd3d2c17?w=100&auto=format&fit=crop&q=80'">
        <div class="search-item-meta">
          <div class="search-item-title">${item.title}</div>
          <div class="search-item-sub">
            <span>${item.channel}</span>
            <span>•</span>
            <span>${item.formatted_duration}</span>
          </div>
        </div>
        <button class="search-action-btn" onclick="event.stopPropagation(); queueTrackFromSearch('${queryPayload}')">
          <i data-lucide="plus"></i> Queue
        </button>
      </div>
    `;
  });

  container.innerHTML = html;
  lucide.createIcons({ roots: [container] });
}

async function queueTrackFromSearch(encodedQuery) {
  const query = decodeURIComponent(encodedQuery);
  document.getElementById("searchResultsDropdown").style.display = "none";
  document.getElementById("globalSearchInput").value = "";
  document.getElementById("searchClearBtn").style.display = "none";
  showToast("Adding song to queue...", "info");

  try {
    await apiCall("/api/queue/add", "POST", { query });
    showToast("Added to queue!", "success");
  } catch (e) {
    showToast("Could not add track", "error");
  }
}

// ==========================================
// Playback Control Event Listeners (with Instant 0ms Optimistic Updates)
// ==========================================
function initEventListeners() {
  // Play/Pause with instant optimistic toggle
  document.getElementById("playPauseBtn").addEventListener("click", async () => {
    if (!currentPlaybackState) return;
    const willPlay = !currentPlaybackState.is_playing || currentPlaybackState.is_paused;
    
    // Instant optimistic update
    currentPlaybackState.is_playing = willPlay;
    currentPlaybackState.is_paused = !willPlay;
    setButtonIcon(document.getElementById("playPauseBtn"), willPlay ? "pause" : "play");
    document.getElementById("trackWaveAnim").style.display = willPlay ? "flex" : "none";
    document.getElementById("heroEqBars").style.display = willPlay ? "flex" : "none";

    if (willPlay) {
      await apiCall("/api/playback/play", "POST");
    } else {
      await apiCall("/api/playback/pause", "POST");
    }
  });

  // Next / Skip
  document.getElementById("nextBtn").addEventListener("click", async () => {
    showToast("Skipping to next track...", "info");
    await apiCall("/api/playback/skip", "POST");
  });

  // Previous
  document.getElementById("prevBtn").addEventListener("click", async () => {
    showToast("Returning to previous track...", "info");
    await apiCall("/api/playback/previous", "POST");
  });

  // Shuffle Queue
  document.getElementById("shuffleBtn").addEventListener("click", async () => {
    if (currentPlaybackState && currentPlaybackState.queue && currentPlaybackState.queue.length > 1) {
      currentPlaybackState.queue.sort(() => Math.random() - 0.5);
      renderQueue(currentPlaybackState.queue);
    }
    await apiCall("/api/queue/shuffle", "POST");
    showToast("Queue shuffled", "info");
  });

  document.getElementById("shuffleQueueBtn").addEventListener("click", async () => {
    if (currentPlaybackState && currentPlaybackState.queue && currentPlaybackState.queue.length > 1) {
      currentPlaybackState.queue.sort(() => Math.random() - 0.5);
      renderQueue(currentPlaybackState.queue);
    }
    await apiCall("/api/queue/shuffle", "POST");
    showToast("Queue shuffled", "info");
  });

  // Loop Mode with instant optimistic cycle (Off -> Track -> Queue -> Off)
  const loopBtn = document.getElementById("loopBtn");
  loopBtn.addEventListener("click", async () => {
    if (!currentPlaybackState) return;
    let curMode = currentPlaybackState.loop_mode || "off";
    let nextMode = "off";
    if (curMode === "off") nextMode = "track";
    else if (curMode === "track") nextMode = "queue";
    else nextMode = "off";

    // Instant optimistic update
    currentPlaybackState.loop_mode = nextMode;
    if (nextMode === "track") {
      loopBtn.classList.add("active");
      loopBtn.title = "Loop Mode: Current Track";
      setButtonIcon(loopBtn, "repeat-1");
    } else if (nextMode === "queue") {
      loopBtn.classList.add("active");
      loopBtn.title = "Loop Mode: Entire Queue";
      setButtonIcon(loopBtn, "repeat");
    } else {
      loopBtn.classList.remove("active");
      loopBtn.title = "Loop Mode: Off";
      setButtonIcon(loopBtn, "repeat");
    }

    await apiCall("/api/playback/loop", "POST", { mode: nextMode });
    showToast(`Loop mode: ${nextMode.toUpperCase()}`, "info");
  });

  // Autoplay Toggle with instant optimistic UI update
  const toggleAutoplay = async () => {
    const curState = currentPlaybackState ? currentPlaybackState.autoplay_enabled : false;
    const nextState = !curState;

    // Instant optimistic UI update
    if (currentPlaybackState) currentPlaybackState.autoplay_enabled = nextState;
    const autoplayToggleBtn = document.getElementById("autoplayToggleBtn");
    const bottomAutoplayBtn = document.getElementById("bottomAutoplayBtn");
    const autoplayPill = document.getElementById("autoplayPill");

    if (autoplayToggleBtn) {
      autoplayToggleBtn.classList.toggle("active", nextState);
      autoplayToggleBtn.title = nextState ? "Autoplay is ON (Click to disable)" : "Autoplay is OFF (Click to enable)";
    }
    if (bottomAutoplayBtn) {
      bottomAutoplayBtn.classList.toggle("active", nextState);
      bottomAutoplayBtn.title = nextState ? "Autoplay: ON (Continuous playback)" : "Autoplay: OFF (Click to enable)";
    }
    if (autoplayPill) {
      autoplayPill.textContent = nextState ? "ON" : "OFF";
    }

    await apiCall("/api/playback/autoplay", "POST", { autoplay: nextState });
    showToast(`Autoplay: ${nextState ? "ENABLED ✨" : "DISABLED"}`, "info");
  };

  const autoplayToggleBtn = document.getElementById("autoplayToggleBtn");
  if (autoplayToggleBtn) autoplayToggleBtn.addEventListener("click", toggleAutoplay);

  const bottomAutoplayBtn = document.getElementById("bottomAutoplayBtn");
  if (bottomAutoplayBtn) bottomAutoplayBtn.addEventListener("click", toggleAutoplay);

  // Clear Queue
  document.getElementById("clearQueueBtn").addEventListener("click", async () => {
    if (confirm("Are you sure you want to clear the upcoming queue?")) {
      if (currentPlaybackState) currentPlaybackState.queue = [];
      renderQueue([]);
      await apiCall("/api/queue/clear", "POST");
      showToast("Queue cleared", "info");
    }
  });

  // Stop & Disconnect
  document.getElementById("stopDisconnectBtn").addEventListener("click", async () => {
    if (currentPlaybackState) {
      currentPlaybackState.is_playing = false;
      currentPlaybackState.is_paused = false;
      currentPlaybackState.current_track = null;
      currentPlaybackState.queue = [];
      updatePlayerState(currentPlaybackState);
    }
    await apiCall("/api/playback/stop", "POST");
    await apiCall("/api/voice/leave", "POST");
    showToast("Playback stopped & disconnected from voice", "info");
  });

  // Seek Slider
  const seekInput = document.getElementById("seekRangeInput");
  seekInput.addEventListener("input", (e) => {
    isSeeking = true;
    const pct = parseFloat(e.target.value);
    const dur = currentPlaybackState && currentPlaybackState.current_track ? currentPlaybackState.current_track.duration : 0;
    const targetSeconds = (pct / 100) * dur;
    document.getElementById("seekSliderFill").style.width = `${pct}%`;
    document.getElementById("seekSliderHandle").style.left = `${pct}%`;
    document.getElementById("currentTimeStamp").textContent = formatTime(targetSeconds);
  });

  seekInput.addEventListener("change", async (e) => {
    const pct = parseFloat(e.target.value);
    const dur = currentPlaybackState && currentPlaybackState.current_track ? currentPlaybackState.current_track.duration : 0;
    const targetSeconds = (pct / 100) * dur;
    if (currentPlaybackState) currentPlaybackState.position = targetSeconds;
    updateScrubber(targetSeconds, dur);
    await apiCall("/api/playback/seek", "POST", { seconds: targetSeconds });
    setTimeout(() => {
      isSeeking = false;
    }, 400);
  });

  // Click on Hero Progress Bar to seek
  const heroProgressContainer = document.getElementById("heroProgressContainer");
  if (heroProgressContainer) {
    heroProgressContainer.addEventListener("click", async (e) => {
      const rect = heroProgressContainer.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const pct = Math.max(0, Math.min(100, (clickX / rect.width) * 100));
      const dur = currentPlaybackState && currentPlaybackState.current_track ? currentPlaybackState.current_track.duration : 0;
      if (dur > 0) {
        const targetSeconds = (pct / 100) * dur;
        if (currentPlaybackState) currentPlaybackState.position = targetSeconds;
        updateScrubber(targetSeconds, dur);
        await apiCall("/api/playback/seek", "POST", { seconds: targetSeconds });
      }
    });
  }

  // Volume Slider
  const volumeRange = document.getElementById("volumeRange");
  volumeRange.addEventListener("input", async (e) => {
    const vol = parseInt(e.target.value);
    document.getElementById("volumePercent").textContent = `${vol}%`;
    const volIconName = vol === 0 ? "volume-x" : (vol < 50 ? "volume-1" : "volume-2");
    setButtonIcon(document.getElementById("volumeMuteBtn"), volIconName);
    await apiCall("/api/playback/volume", "POST", { volume: vol });
  });

  // Mute / Unmute Toggle Button with instant optimistic update
  document.getElementById("volumeMuteBtn").addEventListener("click", async () => {
    if (!currentPlaybackState) return;
    const currentVol = currentPlaybackState.volume;
    let targetVol = 0;

    if (currentVol > 0) {
      lastVolumeBeforeMute = currentVol;
      targetVol = 0;
    } else {
      targetVol = lastVolumeBeforeMute || 80;
    }

    currentPlaybackState.volume = targetVol;
    volumeRange.value = targetVol;
    document.getElementById("volumePercent").textContent = `${targetVol}%`;
    const volIconName = targetVol === 0 ? "volume-x" : (targetVol < 50 ? "volume-1" : "volume-2");
    setButtonIcon(document.getElementById("volumeMuteBtn"), volIconName);

    await apiCall("/api/playback/volume", "POST", { volume: targetVol });
  });

  // Quick Equalizer Button in Bottom Bar
  document.getElementById("eqQuickBtn").addEventListener("click", () => {
    switchTab("audioFx");
  });

  // Save to Playlist Trigger from Queue Toolbar
  const saveToPlaylistBtn = document.getElementById("saveToPlaylistBtn");
  if (saveToPlaylistBtn) {
    saveToPlaylistBtn.addEventListener("click", () => {
      openSaveToPlaylistModal("current");
    });
  }

  // Add to Playlist trigger from Bottom Player Bar Heart
  const bottomFavBtn = document.getElementById("addToPlaylistModalBtn");
  if (bottomFavBtn) {
    bottomFavBtn.addEventListener("click", () => {
      openSaveToPlaylistModal("current");
    });
  }

  // Modals Trigger Handlers
  setupModals();
}

// ==========================================
// Navigation & Tabs
// ==========================================
function initNavigation() {
  document.querySelectorAll(".sidebar-nav li").forEach((navItem) => {
    navItem.addEventListener("click", () => {
      const tabName = navItem.getAttribute("data-tab");
      switchTab(tabName);
    });
  });

  document.getElementById("brandLogo").addEventListener("click", () => {
    switchTab("queue");
  });
}

function switchTab(tabName) {
  // Update sidebar active states
  document.querySelectorAll(".sidebar-nav li").forEach((li) => {
    li.classList.toggle("active", li.getAttribute("data-tab") === tabName);
  });

  // Hide all tabs
  document.querySelectorAll(".tab-content").forEach((tab) => (tab.style.display = "none"));

  // Show target tab
  const targetTab = document.getElementById(`${tabName}Tab`);
  if (targetTab) {
    targetTab.style.display = "flex";
  }
}

// ==========================================
// Playlists Manager (LocalStorage + DB Sync + Presets)
// ==========================================
const PLAYLISTS_STORAGE_KEY = "djpreet_custom_playlists";

function getStoredPlaylists() {
  try {
    let raw = localStorage.getItem(PLAYLISTS_STORAGE_KEY);
    if (!raw) {
      raw = localStorage.getItem("flavibot_custom_playlists");
    }
    return raw ? JSON.parse(raw) : [];
  } catch (e) {
    return [];
  }
}

async function saveStoredPlaylists(playlists) {
  localStorage.setItem(PLAYLISTS_STORAGE_KEY, JSON.stringify(playlists));
  renderCustomPlaylists();
  try {
    for (const pl of playlists) {
      await apiCall("/api/playlists", "POST", pl);
    }
  } catch (e) {
    console.warn("DB Playlist sync warning:", e);
  }
}

async function initPlaylists() {
  renderCustomPlaylists();
  try {
    const data = await apiCall("/api/playlists");
    if (data && data.playlists && data.playlists.length > 0) {
      localStorage.setItem(PLAYLISTS_STORAGE_KEY, JSON.stringify(data.playlists));
      renderCustomPlaylists();
    }
  } catch (e) {}

  // Create playlist modal button in Playlists Tab
  document.getElementById("createPlaylistBtn").addEventListener("click", () => {
    document.getElementById("createPlaylistModal").style.display = "flex";
    document.getElementById("newPlaylistNameInput").value = "";
    document.getElementById("newPlaylistDescInput").value = "";
  });

  // Confirm create playlist
  document.getElementById("confirmCreatePlaylistBtn").addEventListener("click", () => {
    const name = document.getElementById("newPlaylistNameInput").value.trim();
    const desc = document.getElementById("newPlaylistDescInput").value.trim();
    if (!name) {
      showToast("Please enter a playlist name", "error");
      return;
    }

    const playlists = getStoredPlaylists();
    playlists.push({
      id: "pl_" + Date.now(),
      name,
      description: desc || "Custom user playlist",
      tracks: [],
      created_at: Date.now()
    });

    saveStoredPlaylists(playlists);
    document.getElementById("createPlaylistModal").style.display = "none";
    showToast(`Playlist "${name}" created!`, "success");
  });

  // Preset Card click handlers
  document.querySelectorAll(".preset-card").forEach((card) => {
    const preset = card.getAttribute("data-preset");
    const loadBtn = card.querySelector(".preset-load-btn");
    loadBtn.addEventListener("click", () => queuePreset(preset));
  });

  // Modal Target Switchers: Current Song vs Entire Queue
  const targetCurrentBtn = document.getElementById("saveTargetCurrentBtn");
  const targetQueueBtn = document.getElementById("saveTargetQueueBtn");

  if (targetCurrentBtn) {
    targetCurrentBtn.addEventListener("click", () => {
      setSaveModalTarget("current");
    });
  }

  if (targetQueueBtn) {
    targetQueueBtn.addEventListener("click", () => {
      setSaveModalTarget("queue");
    });
  }

  // Quick New Playlist Save from Modal
  const saveToNewBtn = document.getElementById("saveToNewPlaylistBtn");
  if (saveToNewBtn) {
    saveToNewBtn.addEventListener("click", async () => {
      const nameInput = document.getElementById("quickNewPlaylistName");
      const name = nameInput ? nameInput.value.trim() : "";
      if (!name) {
        showToast("Please enter a name for the new playlist", "error");
        return;
      }

      const tracksToSave = getTracksForCurrentSaveTarget();
      if (!tracksToSave || tracksToSave.length === 0) {
        showToast("No tracks available to save", "error");
        return;
      }

      const playlists = getStoredPlaylists();
      const newPlaylist = {
        id: "pl_" + Date.now(),
        name,
        description: `${tracksToSave.length} track${tracksToSave.length === 1 ? "" : "s"} saved on ${new Date().toLocaleDateString()}`,
        tracks: tracksToSave.map(formatTrackForPlaylist),
        created_at: Date.now()
      };

      playlists.push(newPlaylist);
      await saveStoredPlaylists(playlists);

      if (nameInput) nameInput.value = "";
      document.getElementById("saveToPlaylistModal").style.display = "none";
      showToast(`Saved ${tracksToSave.length} track${tracksToSave.length === 1 ? "" : "s"} to new playlist "${name}"!`, "success");
    });
  }
}

function formatTrackForPlaylist(t) {
  return {
    id: t.id,
    title: t.title,
    channel: t.channel,
    duration: t.duration || 0,
    formatted_duration: t.formatted_duration || formatTime(t.duration),
    webpage_url: t.webpage_url || "",
    thumbnail: t.thumbnail || ""
  };
}

function getTracksForCurrentSaveTarget() {
  if (currentSaveTargetMode === "specific" && specificTrackToSave) {
    return [specificTrackToSave];
  }
  if (currentSaveTargetMode === "current") {
    if (currentPlaybackState && currentPlaybackState.current_track) {
      return [currentPlaybackState.current_track];
    }
    if (currentPlaybackState && currentPlaybackState.queue && currentPlaybackState.queue.length > 0) {
      return [currentPlaybackState.queue[0]];
    }
    return [];
  }
  if (currentSaveTargetMode === "queue") {
    const list = [];
    if (currentPlaybackState && currentPlaybackState.current_track) {
      list.push(currentPlaybackState.current_track);
    }
    if (currentPlaybackState && currentPlaybackState.queue) {
      list.push(...currentPlaybackState.queue);
    }
    return list;
  }
  return [];
}

function setSaveModalTarget(mode) {
  currentSaveTargetMode = mode;
  const targetCurrentBtn = document.getElementById("saveTargetCurrentBtn");
  const targetQueueBtn = document.getElementById("saveTargetQueueBtn");
  const previewTitle = document.getElementById("savePreviewTitle");
  const subtitle = document.getElementById("saveToPlaylistTargetInfo");

  if (targetCurrentBtn) targetCurrentBtn.classList.toggle("active", mode === "current");
  if (targetQueueBtn) targetQueueBtn.classList.toggle("active", mode === "queue");

  if (mode === "current") {
    const curr = currentPlaybackState ? currentPlaybackState.current_track : null;
    previewTitle.textContent = curr ? `🎵 ${curr.title}` : "No active song playing";
    subtitle.textContent = "Save currently playing song into your playlist collection";
  } else if (mode === "queue") {
    const qLen = currentPlaybackState && currentPlaybackState.queue ? currentPlaybackState.queue.length : 0;
    const hasCurr = currentPlaybackState && currentPlaybackState.current_track ? 1 : 0;
    const totalCount = qLen + hasCurr;
    previewTitle.textContent = `📋 Entire Queue (${totalCount} total tracks)`;
    subtitle.textContent = `Save all ${totalCount} active queue tracks into a playlist`;
  }
}

function openSaveToPlaylistModal(defaultMode = "current") {
  specificTrackToSave = null;
  const modal = document.getElementById("saveToPlaylistModal");
  const list = document.getElementById("playlistSelectList");
  const queueBadge = document.getElementById("saveQueueCountBadge");
  const playlists = getStoredPlaylists();

  const qLen = currentPlaybackState && currentPlaybackState.queue ? currentPlaybackState.queue.length : 0;
  const hasCurr = currentPlaybackState && currentPlaybackState.current_track ? 1 : 0;
  if (queueBadge) queueBadge.textContent = qLen + hasCurr;

  setSaveModalTarget(defaultMode);

  if (playlists.length === 0) {
    list.innerHTML = `<div style="padding: 12px; text-align: center; color: var(--text-muted); font-size: 0.82rem;">No playlists created yet. Type a name below to create and save!</div>`;
  } else {
    list.innerHTML = playlists.map((pl) => `
      <div class="playlist-select-item" onclick="saveTargetTracksToExistingPlaylist('${pl.id}')">
        <span class="playlist-select-name"><i data-lucide="music-2"></i> ${pl.name}</span>
        <span class="playlist-select-count">${pl.tracks.length} tracks • Click to Add</span>
      </div>
    `).join("");
    lucide.createIcons({ roots: [list] });
  }

  modal.style.display = "flex";
}

function openSaveModalForTrack(encodedTrackJson) {
  specificTrackToSave = JSON.parse(decodeURIComponent(encodedTrackJson));
  currentSaveTargetMode = "specific";

  const modal = document.getElementById("saveToPlaylistModal");
  const list = document.getElementById("playlistSelectList");
  const previewTitle = document.getElementById("savePreviewTitle");
  const subtitle = document.getElementById("saveToPlaylistTargetInfo");
  const playlists = getStoredPlaylists();

  previewTitle.textContent = `🎵 ${specificTrackToSave.title}`;
  subtitle.textContent = "Save this specific track into your playlist collection";

  if (playlists.length === 0) {
    list.innerHTML = `<div style="padding: 12px; text-align: center; color: var(--text-muted); font-size: 0.82rem;">No playlists created yet. Type a name below to create and save!</div>`;
  } else {
    list.innerHTML = playlists.map((pl) => `
      <div class="playlist-select-item" onclick="saveTargetTracksToExistingPlaylist('${pl.id}')">
        <span class="playlist-select-name"><i data-lucide="music-2"></i> ${pl.name}</span>
        <span class="playlist-select-count">${pl.tracks.length} tracks • Click to Add</span>
      </div>
    `).join("");
    lucide.createIcons({ roots: [list] });
  }

  modal.style.display = "flex";
}

async function saveTargetTracksToExistingPlaylist(playlistId) {
  const tracksToSave = getTracksForCurrentSaveTarget();
  if (!tracksToSave || tracksToSave.length === 0) {
    showToast("No tracks available to save", "error");
    return;
  }

  const playlists = getStoredPlaylists();
  const pl = playlists.find((p) => p.id === playlistId);
  if (!pl) {
    showToast("Playlist not found", "error");
    return;
  }

  for (const t of tracksToSave) {
    pl.tracks.push(formatTrackForPlaylist(t));
  }

  await saveStoredPlaylists(playlists);
  document.getElementById("saveToPlaylistModal").style.display = "none";
  showToast(`Added ${tracksToSave.length} track${tracksToSave.length === 1 ? "" : "s"} to playlist "${pl.name}"!`, "success");
}

function renderCustomPlaylists() {
  const container = document.getElementById("customPlaylistsGrid");
  const countBadge = document.getElementById("sidebarPlaylistCount");
  const playlists = getStoredPlaylists();

  countBadge.textContent = playlists.length + 5; // 5 presets + custom

  if (playlists.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1 / -1; padding: 24px; text-align: center; color: var(--text-muted); background: var(--bg-card); border-radius: var(--radius-md); border: 1px dashed var(--border-subtle);">
        <p>No custom playlists yet. Click <strong>"Create Playlist"</strong> to create one or click <strong>"Save to Playlist"</strong> in the queue!</p>
      </div>
    `;
    return;
  }

  let html = "";
  playlists.forEach((pl) => {
    const trackCount = pl.tracks.length;
    html += `
      <div class="custom-playlist-card" data-id="${pl.id}">
        <div>
          <h4>${pl.name}</h4>
          <p>${pl.description || "Custom playlist"}</p>
          <span class="preset-meta">${trackCount} track${trackCount === 1 ? "" : "s"}</span>
        </div>
        <div class="custom-card-actions">
          <button class="playlist-action-btn" onclick="queueCustomPlaylist('${pl.id}')">
            <i data-lucide="play"></i> Queue All
          </button>
          <button class="custom-card-delete-btn" onclick="deleteCustomPlaylist('${pl.id}')" title="Delete playlist">
            <i data-lucide="trash-2"></i>
          </button>
        </div>
      </div>
    `;
  });

  container.innerHTML = html;
  lucide.createIcons({ roots: [container] });
}

async function queuePreset(presetKey) {
  const tracks = PRESET_PLAYLISTS[presetKey];
  if (!tracks) return;

  showToast(`Queueing ${tracks.length} preset tracks...`, "info");
  for (const query of tracks) {
    await apiCall("/api/queue/add", "POST", { query });
  }
  showToast(`Loaded ${tracks.length} tracks into queue!`, "success");
}

async function queueCustomPlaylist(playlistId) {
  const playlists = getStoredPlaylists();
  const pl = playlists.find((p) => p.id === playlistId);
  if (!pl || pl.tracks.length === 0) {
    showToast("This playlist is empty! Add songs first.", "info");
    return;
  }

  showToast(`Queueing ${pl.tracks.length} tracks from "${pl.name}"...`, "info");
  for (const track of pl.tracks) {
    await apiCall("/api/queue/add", "POST", { query: track.webpage_url || track.title });
  }
  showToast(`Queued all tracks from "${pl.name}"!`, "success");
}

async function deleteCustomPlaylist(playlistId) {
  if (confirm("Are you sure you want to delete this playlist?")) {
    let playlists = getStoredPlaylists();
    playlists = playlists.filter((p) => p.id !== playlistId);
    localStorage.setItem(PLAYLISTS_STORAGE_KEY, JSON.stringify(playlists));
    renderCustomPlaylists();
    try {
      await apiCall("/api/playlists/delete", "POST", { id: playlistId });
    } catch (e) {}
    showToast("Playlist deleted", "info");
  }
}

// ==========================================
// History View
// ==========================================
function renderHistory(history) {
  const container = document.getElementById("historyItemsList");
  const sidebarHistoryCount = document.getElementById("sidebarHistoryCount");

  const count = history ? history.length : 0;
  sidebarHistoryCount.textContent = count;

  if (!history || history.length === 0) {
    container.innerHTML = `
      <div class="empty-state-card">
        <div class="empty-state-icon"><i data-lucide="history"></i></div>
        <h3 class="empty-state-title">No history yet</h3>
        <p class="empty-state-desc">Songs played in your voice channel will appear here for easy replay!</p>
      </div>
    `;
    lucide.createIcons({ roots: [container] });
    return;
  }

  let html = "";
  history.forEach((track) => {
    const thumb = track.thumbnail || "https://images.unsplash.com/photo-1614613535308-eb5fbd3d2c17?w=100&auto=format&fit=crop&q=80";
    const duration = track.formatted_duration || formatTime(track.duration);

    html += `
      <div class="history-item">
        <img src="${thumb}" alt="${track.title}" class="history-thumb" onerror="this.src='https://images.unsplash.com/photo-1614613535308-eb5fbd3d2c17?w=100&auto=format&fit=crop&q=80'">
        <div class="history-info">
          <div class="history-title" title="${track.title}">${track.title}</div>
          <div class="history-meta">
            <span>${track.channel}</span>
            <span>•</span>
            <span>${duration}</span>
          </div>
        </div>
        <button class="history-requeue-btn" onclick="requeueHistoryTrack('${encodeURIComponent(track.webpage_url || track.title)}')">
          <i data-lucide="rotate-ccw"></i> Re-play
        </button>
      </div>
    `;
  });

  container.innerHTML = html;
  lucide.createIcons({ roots: [container] });
}

async function requeueHistoryTrack(encodedQuery) {
  const query = decodeURIComponent(encodedQuery);
  showToast("Re-queueing track...", "info");
  await apiCall("/api/queue/add", "POST", { query });
  showToast("Track added to queue!", "success");
}

document.getElementById("clearHistoryBtn").addEventListener("click", async () => {
  if (confirm("Are you sure you want to clear your playback history?")) {
    if (currentPlaybackState) currentPlaybackState.history = [];
    renderHistory([]);
    await apiCall("/api/history/clear", "POST");
    showToast("History cleared", "info");
  }
});

// ==========================================
// Audio FX & Equalizer View (Instant Optimistic Update)
// ==========================================
function initAudioFx() {
  document.querySelectorAll(".fx-card").forEach((card) => {
    card.addEventListener("click", async () => {
      const filter = card.getAttribute("data-filter");
      
      // Instant optimistic UI update
      document.querySelectorAll(".fx-card").forEach((c) => c.classList.remove("active"));
      card.classList.add("active");
      
      const activeFilterName = document.getElementById("activeFilterName");
      if (activeFilterName) activeFilterName.textContent = formatFilterName(filter);
      
      const filterDot = document.getElementById("filterDot");
      const sidebarFxBadge = document.getElementById("sidebarFxBadge");
      const heroFilterBadge = document.getElementById("heroFilterBadge");
      
      if (filter !== "none") {
        if (filterDot) filterDot.style.display = "block";
        if (sidebarFxBadge) sidebarFxBadge.style.display = "inline-block";
        if (heroFilterBadge) {
          heroFilterBadge.style.display = "inline-block";
          heroFilterBadge.textContent = filter.toUpperCase();
        }
      } else {
        if (filterDot) filterDot.style.display = "none";
        if (sidebarFxBadge) sidebarFxBadge.style.display = "none";
        if (heroFilterBadge) heroFilterBadge.style.display = "none";
      }

      await apiCall("/api/playback/filter", "POST", { filter });
      showToast(`Audio Effect applied: ${formatFilterName(filter)}`, "success");
    });
  });
}

// ==========================================
// Commands Cheatsheet View
// ==========================================
function initCommandsCheatsheet() {
  document.querySelectorAll(".command-card").forEach((card) => {
    card.addEventListener("click", () => {
      const cmd = card.getAttribute("data-copy");
      navigator.clipboard.writeText(cmd).then(() => {
        showToast(`Copied "${cmd}" to clipboard!`, "success");
      });
    });
  });
}

// ==========================================
// Modals Setup
// ==========================================
function setupModals() {
  // Close buttons
  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const modalId = btn.getAttribute("data-close");
      const modal = document.getElementById(modalId);
      if (modal) modal.style.display = "none";
    });
  });

  // Close when clicking modal backdrop
  document.querySelectorAll(".modal-overlay").forEach((overlay) => {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.style.display = "none";
    });
  });

  // 1. Voice Connection Modal
  const openVoiceModal = async () => {
    document.getElementById("voiceModal").style.display = "flex";
    await loadGuildsAndChannels();
  };
  document.getElementById("voiceConnectBtn").addEventListener("click", openVoiceModal);
  document.getElementById("sidebarVoiceActionBtn").addEventListener("click", openVoiceModal);

  document.getElementById("joinVoiceModalBtn").addEventListener("click", async () => {
    const guildId = document.getElementById("guildSelectDropdown").value;
    const channelId = document.getElementById("voiceChannelSelectDropdown").value;
    if (!channelId) {
      showToast("Please select a voice channel", "error");
      return;
    }

    try {
      const res = await apiCall("/api/voice/join", "POST", { guild_id: guildId, channel_id: channelId });
      showToast(`Connected to ${res.channel_name}!`, "success");
      document.getElementById("voiceModal").style.display = "none";
    } catch (e) {
      showToast("Failed to join voice channel", "error");
    }
  });

  document.getElementById("leaveVoiceModalBtn").addEventListener("click", async () => {
    await apiCall("/api/voice/leave", "POST");
    showToast("Disconnected from voice", "info");
    document.getElementById("voiceModal").style.display = "none";
  });

  // 2. Settings Modal
  document.getElementById("openSettingsBtn").addEventListener("click", () => {
    document.getElementById("settingsModal").style.display = "flex";
  });

  document.getElementById("toggleTokenVisibility").addEventListener("click", () => {
    const input = document.getElementById("botTokenInput");
    input.type = input.type === "password" ? "text" : "password";
  });

  document.getElementById("saveTokenBtn").addEventListener("click", async () => {
    const token = document.getElementById("botTokenInput").value.trim();
    if (!token) {
      showToast("Please enter a bot token", "error");
      return;
    }
    await apiCall("/api/settings/token", "POST", { token });
    showToast("Token saved! Discord bot will connect momentarily.", "success");
    document.getElementById("settingsModal").style.display = "none";
  });
}

// ==========================================
// Guilds and Voice Channels Fetcher
// ==========================================
async function loadGuildsAndChannels() {
  try {
    const data = await apiCall("/api/guilds");
    const guildSelect = document.getElementById("guildSelectDropdown");
    const channelSelect = document.getElementById("voiceChannelSelectDropdown");
    const statusBox = document.getElementById("channelStatusText");

    if (!data.guilds || data.guilds.length === 0) {
      guildSelect.innerHTML = `<option value="">No servers detected. Invite bot first!</option>`;
      channelSelect.innerHTML = `<option value="">No voice channels</option>`;
      statusBox.textContent = "Bot is not invited to any servers yet.";
      return;
    }

    guildSelect.innerHTML = data.guilds.map((g) => `<option value="${g.id}">${g.name}</option>`).join("");

    const updateChannels = () => {
      const selectedGuild = data.guilds.find((g) => g.id === guildSelect.value) || data.guilds[0];
      if (selectedGuild && selectedGuild.voice_channels) {
        channelSelect.innerHTML = selectedGuild.voice_channels
          .map((vc) => `<option value="${vc.id}">🔊 ${vc.name} (${vc.user_count} users)</option>`)
          .join("");
        statusBox.textContent = `Server: ${selectedGuild.name} (${selectedGuild.voice_channels.length} voice channels)`;
      }
    };

    guildSelect.addEventListener("change", updateChannels);
    updateChannels();
  } catch (e) {
    console.error("Failed to load guilds:", e);
  }
}

// ==========================================
// Bot Status & Latency
// ==========================================
async function loadBotStatus() {
  try {
    const data = await apiCall("/api/status");
    updateBotStatus(data);
  } catch (e) {
    console.warn("Status fetch failed:", e);
  }
}

function updateBotStatus(statusData) {
  if (!statusData) return;
  const pingVal = document.getElementById("pingValue");
  const pingMs = statusData.ping_ms || 24;
  pingVal.textContent = `${pingMs} ms`;

  const botNameEl = document.getElementById("sidebarBotName");
  const botStatusEl = document.getElementById("sidebarBotStatus");
  if (botNameEl && statusData.bot_name) botNameEl.textContent = statusData.bot_name;
  if (botStatusEl) {
    botStatusEl.textContent = statusData.is_ready ? "Online & Ready" : "Bot Offline";
    botStatusEl.style.color = statusData.is_ready ? "var(--accent-green)" : "var(--accent-danger)";
  }

  const tokenStatusBox = document.getElementById("tokenStatusBox");
  const tokenStatusText = document.getElementById("tokenStatusText");
  const tokenInput = document.getElementById("botTokenInput");

  if (statusData.token_configured) {
    if (tokenStatusBox) tokenStatusBox.className = "channel-status-box connected";
    if (tokenStatusText) tokenStatusText.innerHTML = `<i data-lucide="check-circle"></i> Token is loaded & active in .env`;
    if (tokenInput) tokenInput.placeholder = "•••••••••••••••••••••••• (Active in .env)";
  } else {
    if (tokenStatusBox) tokenStatusBox.className = "channel-status-box";
    if (tokenStatusText) tokenStatusText.innerHTML = `<i data-lucide="alert-triangle"></i> Token is not configured yet`;
    if (tokenInput) tokenInput.placeholder = "Paste your bot token here...";
    showToast("⚠️ Discord Token not set! Click the Settings gear ⚙️ to configure your bot.", "info");
  }
  lucide.createIcons({ roots: [document.getElementById("settingsModal")] });
}

// ==========================================
// Toast Notifications
// ==========================================
function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  const toast = document.createElement("div");
  toast.className = `toast-message toast-${type}`;
  
  let icon = "info";
  if (type === "success") icon = "check-circle";
  if (type === "error") icon = "alert-triangle";

  toast.innerHTML = `<i data-lucide="${icon}"></i> <span>${message}</span>`;
  container.appendChild(toast);
  lucide.createIcons({ roots: [toast] });

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(50px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}
