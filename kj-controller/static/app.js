/* Nomad KJ Control — Application Logic */

// --- Logging ---

const logArea = document.getElementById('log-area');

function log(message, type = 'info') {
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    logArea.prepend(entry);
}

// --- Feedback (#12) ---

function flashElement(el, type = 'success') {
    el.classList.remove('flash-success', 'flash-error');
    void el.offsetWidth; // force reflow to restart animation
    el.classList.add(type === 'success' ? 'flash-success' : 'flash-error');
}

// --- API ---

async function apiCall(endpoint, body) {
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await response.json();
        if (!response.ok) {
            let errorMessage = data.error || 'API request failed';
            if (data.vlc_status) {
                errorMessage += ` | VLC Status: ${JSON.stringify(data.vlc_status)}`;
            }
            throw new Error(errorMessage);
        }
        return data;
    } catch (error) {
        log(`API Error: ${error.message}`, 'error');
        return null;
    }
}

// --- Download (#9 progress stages) ---

let downloadStageTimers = [];

async function downloadSong() {
    const urlInput = document.getElementById('youtube-url');
    const downloadBtn = document.getElementById('download-btn');
    const downloadStatus = document.getElementById('download-status');
    const downloadStage = document.getElementById('download-stage');
    const url = urlInput.value;
    if (!url) {
        log('Please enter a YouTube URL.', 'error');
        return;
    }
    log(`Downloading: ${url}`);
    downloadBtn.disabled = true;
    downloadStatus.classList.remove('hidden');

    // Staged progress messages
    downloadStageTimers.forEach(t => clearTimeout(t));
    downloadStageTimers = [];
    const stages = [
        { time: 0, text: 'Fetching video info...' },
        { time: 3000, text: 'Downloading video...' },
        { time: 15000, text: 'Still downloading (large file)...' },
        { time: 30000, text: 'Almost there...' },
    ];
    stages.forEach(s => {
        downloadStageTimers.push(setTimeout(() => {
            downloadStage.textContent = s.text;
        }, s.time));
    });

    const data = await apiCall('/download', { url });

    downloadStageTimers.forEach(t => clearTimeout(t));
    downloadStageTimers = [];
    downloadBtn.disabled = false;
    downloadStatus.classList.add('hidden');
    if (data && data.success) {
        log(`Downloaded "${data.title}" successfully!`, 'success');
        urlInput.value = '';
        flashElement(urlInput, 'success');
        await updateMediaList();
    }
}

// --- Playback ---

let currentPlayingPath = null;

async function playMedia(filePath) {
    if (!filePath) {
        log('Cannot play: file path is missing.', 'error');
        return;
    }
    const filename = filePath.split('/').pop();
    log(`Playing: ${filename}`);
    await apiCall('/play', { file_path: filePath });
}

async function deleteMedia(filePath, displayName) {
    // Prevent deletion of currently-playing song (#6)
    if (filePath === currentPlayingPath) {
        log('Cannot delete the currently playing song. Stop it first.', 'error');
        return;
    }
    if (!confirm(`Are you sure you want to delete "${displayName}"?`)) {
        return;
    }
    log(`Deleting: ${displayName}`);
    const data = await apiCall('/delete', { file_path: filePath });
    if (data && data.success) {
        log(`Successfully deleted "${displayName}"`, 'success');
        await updateMediaList();
    }
}

async function rescanMedia() {
    const btn = document.getElementById('rescan-btn');
    btn.disabled = true;
    btn.textContent = 'Scanning...';
    log('Rescanning media folders...');
    const data = await apiCall('/rescan', {});
    btn.disabled = false;
    btn.textContent = 'Rescan Media';
    if (data && data.success) {
        log(`Rescan complete: ${data.count} files found.`, 'success');
        await updateMediaList();
    }
}

async function fixAudio() {
    log('Restarting VLC instances to fix audio...');
    const data = await apiCall('/fix_audio', {});
    if (data && data.success) {
        log('Audio fix applied - VLC instances restarted.', 'success');
    }
}

async function controlPlayback(action) {
    log(`Sending control: ${action}`);
    await apiCall('/control', { action });
}

// --- Volume & Seek (#2 volume labels) ---

function volumePercent(val) {
    return Math.round(val / 256 * 100) + '%';
}

function updateKaraokeVolume(value) {
    document.getElementById('karaoke-volume-label').textContent = volumePercent(value);
    setVolume('karaoke', value);
}

function updateFillerVolume(value) {
    document.getElementById('filler-volume-label').textContent = volumePercent(value);
    setVolume('filler', value);
}

async function setVolume(target, level) {
    await apiCall('/volume', { target, level: parseInt(level) });
}

async function setAudioDevice(device) {
    log(`Switching audio device to: ${device} (VLC will restart)`);
    const data = await apiCall('/audio_device', { device });
    if (data) {
        flashElement(document.getElementById('audio-device'), 'success');
    }
}

let totalVideoLength = 0;
let isSeeking = false;

function updateSeekLabel(position) {
    if (totalVideoLength > 0) {
        const seekTime = parseFloat(position) * totalVideoLength;
        document.getElementById('seek-value').textContent = formatTime(seekTime);
    }
}

async function seekVideo(position) {
    if (totalVideoLength > 0) {
        const seekTime = parseFloat(position) * totalVideoLength;
        log(`Seeking to ${formatTime(seekTime)}`);
        await apiCall('/seek', { time: seekTime });
    }
}

async function setFillerMusic(trackName) {
    log(`Setting filler music to: ${trackName}`);
    const data = await apiCall('/filler_music', { track_name: trackName });
    if (data) {
        flashElement(document.getElementById('filler-selector'), 'success');
    }
}

async function updateFillerMusicList() {
    try {
        const response = await fetch('/filler_music');
        const tracks = await response.json();
        const selector = document.getElementById('filler-selector');
        selector.innerHTML = '';
        tracks.forEach(track => {
            const option = document.createElement('option');
            option.value = track;
            option.textContent = track;
            selector.appendChild(option);
        });
    } catch (error) {
        log('Could not update filler music list.', 'error');
    }
}

// --- Media List ---

let localMediaItems = [];

function loadFolderStates() {
    try {
        return JSON.parse(localStorage.getItem('kj-folder-state') || '{}');
    } catch { return {}; }
}

function saveFolderStates(states) {
    localStorage.setItem('kj-folder-state', JSON.stringify(states));
}

function toggleFolder(folderName) {
    const escapedName = CSS.escape(folderName);
    const container = document.getElementById('folder-' + escapedName);
    const chevron = document.getElementById('chevron-' + escapedName);
    if (!container) return;
    const isCollapsed = container.classList.toggle('collapsed');
    chevron.classList.toggle('expanded', !isCollapsed);
    const states = loadFolderStates();
    states[folderName] = !isCollapsed;
    saveFolderStates(states);
}

function expandAll() {
    document.querySelectorAll('.folder-items').forEach(el => el.classList.remove('collapsed'));
    document.querySelectorAll('.folder-chevron').forEach(el => el.classList.add('expanded'));
    const states = {};
    document.querySelectorAll('.folder-items').forEach(el => {
        states[el.dataset.folder] = true;
    });
    saveFolderStates(states);
}

function collapseAll() {
    document.querySelectorAll('.folder-items').forEach(el => el.classList.add('collapsed'));
    document.querySelectorAll('.folder-chevron').forEach(el => el.classList.remove('expanded'));
    const states = {};
    document.querySelectorAll('.folder-items').forEach(el => {
        states[el.dataset.folder] = false;
    });
    saveFolderStates(states);
}

function createMediaItemLi(item) {
    const li = document.createElement('li');

    const titleSpan = document.createElement('span');
    if (item.channel) {
        const channelSpan = document.createElement('span');
        channelSpan.className = 'channel-tag';
        channelSpan.textContent = `(${item.channel})`;
        titleSpan.textContent = item.display_name + ' ';
        titleSpan.appendChild(channelSpan);
    } else {
        titleSpan.textContent = item.display_name;
    }

    const rightSide = document.createElement('span');
    if (item.is_download) {
        const deleteBtn = document.createElement('button');
        deleteBtn.textContent = 'Delete';
        deleteBtn.className = 'delete-btn';
        deleteBtn.onclick = (e) => {
            e.stopPropagation();
            deleteMedia(item.file_path, item.display_name);
        };
        rightSide.appendChild(deleteBtn);
    }

    li.appendChild(titleSpan);
    li.appendChild(rightSide);
    li.title = 'Click to play';
    li.onclick = () => {
        document.querySelectorAll('#media-list li').forEach(el => el.classList.remove('playing'));
        li.classList.add('playing');
        playMedia(item.file_path);
    };
    return li;
}

function renderFolderView(items) {
    const mediaList = document.getElementById('media-list');
    const mediaCount = document.getElementById('media-count');
    const folderControls = document.getElementById('folder-controls');
    mediaList.innerHTML = '';

    if (items.length === 0) {
        const li = document.createElement('li');
        li.textContent = 'No media files found. Try downloading a song or clicking Rescan.';
        li.style.cursor = 'default';
        mediaList.appendChild(li);
        mediaCount.textContent = '';
        folderControls.classList.add('hidden');
        return;
    }

    mediaCount.textContent = `(${items.length})`;

    const groups = {};
    items.forEach(item => {
        const folder = item.folder_name || 'Unknown';
        if (!groups[folder]) groups[folder] = [];
        groups[folder].push(item);
    });

    const folderNames = Object.keys(groups).sort();
    const showHeaders = folderNames.length > 1;
    folderControls.classList.toggle('hidden', !showHeaders);

    const savedStates = loadFolderStates();

    folderNames.forEach(folderName => {
        if (showHeaders) {
            const isExpanded = savedStates[folderName] !== undefined ? savedStates[folderName] : false;

            const header = document.createElement('li');
            header.className = 'folder-header';
            header.onclick = () => toggleFolder(folderName);

            const chevron = document.createElement('span');
            chevron.className = 'folder-chevron' + (isExpanded ? ' expanded' : '');
            chevron.id = 'chevron-' + CSS.escape(folderName);
            chevron.textContent = '\u25B6';

            header.appendChild(chevron);
            header.appendChild(document.createTextNode(`${folderName} (${groups[folderName].length})`));
            mediaList.appendChild(header);

            const wrapper = document.createElement('li');
            wrapper.style.cssText = 'padding:0; border-bottom:none; display:block;';
            const container = document.createElement('ul');
            container.style.cssText = 'list-style:none; padding:0; margin:0;';
            container.className = 'folder-items' + (isExpanded ? '' : ' collapsed');
            container.id = 'folder-' + CSS.escape(folderName);
            container.dataset.folder = folderName;

            groups[folderName].forEach(item => {
                container.appendChild(createMediaItemLi(item));
            });
            wrapper.appendChild(container);
            mediaList.appendChild(wrapper);
        } else {
            groups[folderName].forEach(item => {
                mediaList.appendChild(createMediaItemLi(item));
            });
        }
    });
}

async function updateMediaList() {
    if (searchActive) return;
    try {
        const response = await fetch('/media');
        localMediaItems = await response.json();
        renderFolderView(localMediaItems);
    } catch (error) {
        log('Could not update media list.', 'error');
    }
}

// --- Status (#1 now-playing, #3 button states) ---

function formatTime(seconds) {
    if (isNaN(seconds) || seconds < 0) return "0:00";
    const min = Math.floor(seconds / 60);
    const sec = Math.floor(seconds % 60).toString().padStart(2, '0');
    return `${min}:${sec}`;
}

function updateNowPlaying(data) {
    const bar = document.getElementById('now-playing-bar');
    const npState = document.getElementById('np-state');
    const npTitle = document.getElementById('np-title');
    const npTime = document.getElementById('np-time');
    const npLength = document.getElementById('np-length');
    const npPause = document.getElementById('np-pause');

    const state = data.state || 'stopped';
    const isActive = state === 'playing' || state === 'paused';

    if (isActive && data.current_playing) {
        bar.classList.remove('hidden');
        npTitle.textContent = data.current_playing;
        npTime.textContent = formatTime(data.time);
        npLength.textContent = formatTime(data.length);

        npState.textContent = state === 'playing' ? 'Playing' : 'Paused';
        npState.className = 'now-playing-state ' + (state === 'playing' ? 'state-playing' : 'state-paused');
        npPause.textContent = state === 'playing' ? 'Pause' : 'Resume';
    } else {
        bar.classList.add('hidden');
    }
}

function updatePlaybackButtons(state) {
    const btnPause = document.getElementById('btn-pause');
    const btnRestart = document.getElementById('btn-restart');
    const btnStop = document.getElementById('btn-stop');

    if (state === 'playing') {
        btnPause.textContent = 'Pause';
        btnPause.disabled = false;
        btnRestart.disabled = false;
        btnStop.disabled = false;
    } else if (state === 'paused') {
        btnPause.textContent = 'Resume';
        btnPause.disabled = false;
        btnRestart.disabled = false;
        btnStop.disabled = false;
    } else {
        btnPause.textContent = 'Pause / Resume';
        btnPause.disabled = false; // Keep enabled so user can always try
        btnRestart.disabled = true;
        btnStop.disabled = true;
    }
}

async function updateStatus() {
    try {
        const response = await fetch('/status');
        const data = await response.json();
        if (response.ok) {
            const state = data.state || 'stopped';
            document.getElementById('player-state').textContent = state;
            document.getElementById('current-filler').textContent = data.current_filler_track || 'None';

            const audioWarning = document.getElementById('audio-warning');
            audioWarning.style.display = data.audio_error ? 'block' : 'none';

            if (data.audio_device) {
                const deviceSelect = document.getElementById('audio-device');
                if (deviceSelect.value !== data.audio_device) {
                    deviceSelect.value = data.audio_device;
                }
            }

            if (data.current_filler_track) {
                const fillerSelect = document.getElementById('filler-selector');
                if (fillerSelect.value !== data.current_filler_track) {
                    fillerSelect.value = data.current_filler_track;
                }
            }

            // Track current playing path for delete protection (#6)
            currentPlayingPath = data.current_playing_path || null;

            totalVideoLength = data.length || 0;
            const seekSlider = document.getElementById('seek-slider');
            if (!isSeeking) {
                if (totalVideoLength > 0) {
                    seekSlider.value = data.time / totalVideoLength;
                    updateSeekLabel(seekSlider.value);
                } else {
                    seekSlider.value = 0;
                    updateSeekLabel(0);
                }
            }

            // Update now-playing bar (#1) and button states (#3)
            updateNowPlaying(data);
            updatePlaybackButtons(state);
        }
    } catch (error) {
        // Don't log periodic status check errors to avoid clutter
    }
}

async function loadAudioDevices() {
    try {
        const response = await fetch('/audio_device');
        const data = await response.json();
        const selector = document.getElementById('audio-device');
        selector.innerHTML = '';
        for (const [key, label] of Object.entries(data.available)) {
            const option = document.createElement('option');
            option.value = key;
            option.textContent = label;
            if (key === data.current) option.selected = true;
            selector.appendChild(option);
        }
    } catch (error) {
        log('Could not load audio devices.', 'error');
    }
}

// --- Display Resolution ---

async function loadDisplayResolutions() {
    try {
        const response = await fetch('/display/resolution');
        const data = await response.json();
        const selector = document.getElementById('display-resolution');
        selector.innerHTML = '';
        if (!data.available || data.available.length === 0) {
            const option = document.createElement('option');
            option.textContent = 'N/A';
            option.disabled = true;
            selector.appendChild(option);
            return;
        }
        data.available.forEach(mode => {
            const option = document.createElement('option');
            option.value = mode;
            option.textContent = mode;
            if (mode === data.current) option.selected = true;
            selector.appendChild(option);
        });
    } catch (error) {
        log('Could not load display resolutions.', 'error');
    }
}

async function setDisplayResolution(resolution) {
    log(`Setting display resolution to ${resolution}...`);
    const data = await apiCall('/display/resolution', { resolution });
    if (data && data.success) {
        log(data.message, 'success');
        flashElement(document.getElementById('display-resolution'), 'success');
    }
}

// --- HDMI Scan ---

async function scanHdmiDevices() {
    const btn = document.querySelector('.hdmi-scan-btn');
    const resultsEl = document.getElementById('hdmi-scan-results');
    btn.disabled = true;
    btn.textContent = 'Scanning...';
    resultsEl.classList.remove('hidden');
    resultsEl.innerHTML = '<div style="padding:6px 8px;color:#888;">Scanning HDMI devices...</div>';

    const data = await apiCall('/audio/scan', {});
    btn.disabled = false;
    btn.textContent = 'Scan HDMI';

    if (!data || !data.devices) {
        resultsEl.innerHTML = '<div style="padding:6px 8px;color:#ef4444;">Scan failed</div>';
        return;
    }

    resultsEl.innerHTML = '';
    const entries = Object.entries(data.devices);
    if (entries.length === 0) {
        resultsEl.innerHTML = '<div style="padding:6px 8px;color:#888;">No HDMI devices found</div>';
        return;
    }

    entries.forEach(([hwId, info]) => {
        const row = document.createElement('div');
        row.className = 'hdmi-device' + (hwId === data.current_hw ? ' active' : '');

        const infoDiv = document.createElement('div');
        infoDiv.className = 'hdmi-device-info';

        const dot = document.createElement('span');
        dot.className = 'hdmi-dot ' + (info.connected ? 'connected' : 'disconnected');
        dot.title = info.connected ? 'Connected' : 'Disconnected';

        const name = document.createElement('span');
        name.className = 'hdmi-device-name';
        name.textContent = info.name;

        const hw = document.createElement('span');
        hw.className = 'hdmi-device-hw';
        hw.textContent = hwId + (hwId === data.current_hw ? ' (current)' : '');

        infoDiv.appendChild(dot);
        infoDiv.appendChild(name);
        infoDiv.appendChild(hw);
        row.appendChild(infoDiv);

        if (hwId !== data.current_hw) {
            const useBtn = document.createElement('button');
            useBtn.className = 'hdmi-use-btn';
            useBtn.textContent = 'Use';
            useBtn.onclick = (e) => {
                e.stopPropagation();
                switchHdmiDevice(hwId);
            };
            row.appendChild(useBtn);
        }

        row.onclick = () => {
            if (hwId !== data.current_hw) switchHdmiDevice(hwId);
        };

        resultsEl.appendChild(row);
    });

    log(`HDMI scan: ${entries.length} devices found`, 'success');
}

async function switchHdmiDevice(hwId) {
    log(`Switching HDMI to ${hwId}...`);
    const data = await apiCall('/audio/switch-hdmi', { device: hwId });
    if (data && data.success) {
        log(data.message, 'success');
        // Refresh the scan results after a brief delay for VLC restart
        document.getElementById('hdmi-scan-results').classList.add('hidden');
        await loadAudioDevices();
    }
}

// --- Catalog Search ---

let searchActive = false;
let searchDebounceTimer = null;

function getFormatBadgeClass(format) {
    if (format === 'cdg+mp3') return 'zip';
    if (format === 'mp4') return 'mp4';
    return 'other';
}

// Character map injected from server (see index.html inline script)
const _latinSpecialMap = window.KJ_CONFIG.latinSpecialMap;
const _latinSpecialRe = new RegExp('[' + Object.keys(_latinSpecialMap).join('').replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ']', 'g');

function normalizeForSearch(str) {
    // NFD decompose + strip combining marks (handles e with accent -> e, etc.)
    let s = str.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    // Handle non-decomposable Latin characters (ø→o, æ→ae, ß→ss, etc.)
    s = s.replace(_latinSpecialRe, m => _latinSpecialMap[m]);
    return s;
}

function filterLocalMedia(query) {
    const terms = normalizeForSearch(query.toLowerCase()).split(/\s+/).filter(t => t);
    return localMediaItems.filter(item => {
        const text = normalizeForSearch(((item.display_name || '') + ' ' + (item.channel || '')).toLowerCase());
        return terms.every(term => text.includes(term));
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function renderUnifiedResults(localResults, catalogResults, query) {
    const mediaList = document.getElementById('media-list');
    const mediaCount = document.getElementById('media-count');
    const searchMeta = document.getElementById('search-meta');
    const folderControls = document.getElementById('folder-controls');
    mediaList.innerHTML = '';
    folderControls.classList.add('hidden');

    const safeQuery = escapeHtml(query);
    const totalCount = localResults.length + catalogResults.length;
    searchMeta.classList.remove('hidden');
    if (totalCount === 0) {
        searchMeta.innerHTML = `No results for "<strong>${safeQuery}</strong>". <a onclick="clearSearch()">Clear search</a>`;
        mediaCount.textContent = '';
        return;
    }

    const parts = [];
    if (localResults.length > 0) parts.push(`${localResults.length} local`);
    if (catalogResults.length > 0) parts.push(`${catalogResults.length} catalog`);
    searchMeta.innerHTML = `${parts.join(' + ')} result${totalCount !== 1 ? 's' : ''} for "<strong>${safeQuery}</strong>" <a onclick="clearSearch()">Clear</a>`;
    mediaCount.textContent = `(${totalCount})`;

    if (localResults.length > 0) {
        if (catalogResults.length > 0) {
            const sectionHeader = document.createElement('li');
            sectionHeader.className = 'section-header';
            sectionHeader.textContent = `Your Library (${localResults.length})`;
            mediaList.appendChild(sectionHeader);
        }
        localResults.forEach(item => {
            mediaList.appendChild(createMediaItemLi(item));
        });
    }

    if (catalogResults.length > 0) {
        if (localResults.length > 0) {
            const sectionHeader = document.createElement('li');
            sectionHeader.className = 'section-header';
            sectionHeader.textContent = `Catalog (${catalogResults.length})`;
            mediaList.appendChild(sectionHeader);
        }
        catalogResults.forEach(item => {
            const li = document.createElement('li');
            li.title = 'Click to play from external drive';
            const detail = document.createElement('div');
            detail.className = 'catalog-detail';

            const titleRow = document.createElement('span');
            const nameText = item.filename.replace(/\.\w+$/, '');
            titleRow.textContent = nameText + ' ';
            const badge = document.createElement('span');
            badge.className = `format-badge ${getFormatBadgeClass(item.format)}`;
            badge.textContent = item.format;
            titleRow.appendChild(badge);
            detail.appendChild(titleRow);

            if (item.folder) {
                const folderSpan = document.createElement('div');
                folderSpan.className = 'catalog-folder';
                let folderDisplay = item.folder;
                folderDisplay = folderDisplay.replace(/^\/mnt\/[^/]+\//, '');
                folderDisplay = folderDisplay.replace(/^\/Volumes\/[^/]+\//, '');
                folderSpan.textContent = folderDisplay;
                folderSpan.title = item.folder;
                detail.appendChild(folderSpan);
            }

            li.appendChild(detail);
            li.onclick = () => {
                document.querySelectorAll('#media-list li').forEach(el => el.classList.remove('playing'));
                li.classList.add('playing');
                playMedia(item.path);
            };
            mediaList.appendChild(li);
        });
    }
}

async function catalogSearch(query) {
    if (!query || query.length < 2) {
        if (searchActive) clearSearch();
        return;
    }
    searchActive = true;
    document.getElementById('search-clear').classList.remove('hidden');

    const localResults = filterLocalMedia(query);
    renderUnifiedResults(localResults, [], query);

    let catalogResults = [];
    try {
        const response = await fetch(`/search?q=${encodeURIComponent(query)}&limit=50`);
        if (response.ok) {
            catalogResults = await response.json();
        }
    } catch (error) {
        // Catalog unavailable, show local results only
    }

    if (searchActive && document.getElementById('catalog-search').value.trim() === query) {
        renderUnifiedResults(localResults, catalogResults, query);
    }
}

function clearSearch() {
    searchActive = false;
    document.getElementById('catalog-search').value = '';
    document.getElementById('search-clear').classList.add('hidden');
    document.getElementById('search-meta').classList.add('hidden');
    renderFolderView(localMediaItems);
}

async function checkCatalogAvailability() {
    try {
        const response = await fetch('/catalog/stats');
        const data = await response.json();
        const searchInput = document.getElementById('catalog-search');
        let placeholder;
        if (data.available) {
            placeholder = `Search your library + ${data.total.toLocaleString()} catalog songs...`;
        } else {
            placeholder = 'Search your library...';
        }
        // Add keyboard shortcut hint on non-touch devices (#8)
        if (!('ontouchstart' in window)) {
            placeholder += '  (press /)';
        }
        searchInput.placeholder = placeholder;
    } catch (error) {
        // Catalog not available - local search still works
    }
}

// --- Overlays (modal #10) ---

const OVERLAY_TYPE_LABELS = {
    ticker: 'Ticker',
    static_text: 'Text',
    image: 'Image',
    countdown: 'Timer',
    qr_code: 'QR',
};

async function loadOverlays() {
    try {
        const response = await fetch('/overlays');
        const overlays = await response.json();
        renderOverlayList(overlays);
    } catch (error) {
        // Overlay API not available
    }
}

function renderOverlayList(overlays) {
    const list = document.getElementById('overlay-list');
    list.innerHTML = '';
    overlays.forEach(overlay => {
        const item = document.createElement('div');
        item.className = 'overlay-item';

        // Toggle switch
        const toggle = document.createElement('label');
        toggle.className = 'overlay-toggle';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = overlay.enabled;
        checkbox.onchange = () => toggleOverlayEnabled(overlay.id);
        const slider = document.createElement('span');
        slider.className = 'slider';
        toggle.appendChild(checkbox);
        toggle.appendChild(slider);

        // Info
        const info = document.createElement('div');
        info.className = 'overlay-item-info';
        const name = document.createElement('div');
        name.className = 'overlay-item-name';
        name.textContent = overlay.name || '(unnamed)';
        if (overlay.show_over_video) {
            const icon = document.createElement('span');
            icon.className = 'overlay-video-icon';
            icon.textContent = '\u25B6';
            icon.title = 'Visible over video';
            name.appendChild(icon);
        }
        const meta = document.createElement('div');
        meta.className = 'overlay-item-meta';
        const badge = document.createElement('span');
        badge.className = 'overlay-type-badge';
        badge.textContent = OVERLAY_TYPE_LABELS[overlay.type] || overlay.type;
        meta.appendChild(badge);
        // Show preview text for text-based overlays
        const previewText = overlay.config?.text || overlay.config?.url || overlay.config?.label || '';
        if (previewText) {
            const preview = document.createTextNode(' ' + (previewText.length > 40 ? previewText.slice(0, 40) + '...' : previewText));
            meta.appendChild(preview);
        }
        info.appendChild(name);
        info.appendChild(meta);

        // Actions
        const actions = document.createElement('div');
        actions.className = 'overlay-item-actions';
        const editBtn = document.createElement('button');
        editBtn.className = 'overlay-edit-btn';
        editBtn.textContent = 'Edit';
        editBtn.onclick = () => editOverlay(overlay);
        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'overlay-delete-btn';
        deleteBtn.textContent = '\u00D7';
        deleteBtn.onclick = () => deleteOverlay(overlay.id, overlay.name);
        actions.appendChild(editBtn);
        actions.appendChild(deleteBtn);

        item.appendChild(toggle);
        item.appendChild(info);
        item.appendChild(actions);
        list.appendChild(item);
    });
}

function onOverlayTypeChange() {
    const type = document.getElementById('overlay-type').value;
    document.querySelectorAll('.overlay-field').forEach(el => {
        const types = el.dataset.types.split(',');
        el.classList.toggle('hidden', !types.includes(type));
    });
    // Set sensible default position based on type
    const posSelect = document.getElementById('overlay-position');
    if (type === 'ticker') {
        posSelect.value = 'bottom';
    } else if (type === 'qr_code') {
        posSelect.value = 'bottom-right';
    } else if (type === 'countdown') {
        posSelect.value = 'top-center';
    } else if (type === 'static_text') {
        posSelect.value = 'top-right';
    } else if (type === 'image') {
        posSelect.value = 'top-right';
    }
}

function onOverlayPositionChange() {
    const pos = document.getElementById('overlay-position').value;
    document.getElementById('overlay-custom-pos').classList.toggle('hidden', pos !== 'custom');
}

function showOverlayForm(overlay) {
    const modal = document.getElementById('overlay-modal');
    const title = document.getElementById('overlay-modal-title');
    modal.classList.remove('hidden');
    title.textContent = overlay ? 'Edit Overlay' : 'Add Overlay';

    // Reset form
    document.getElementById('overlay-edit-id').value = overlay ? overlay.id : '';
    document.getElementById('overlay-name').value = overlay ? overlay.name : '';
    document.getElementById('overlay-type').value = overlay ? overlay.type : 'ticker';
    document.getElementById('overlay-type').disabled = !!overlay;  // Can't change type when editing
    document.getElementById('overlay-enabled').checked = overlay ? overlay.enabled : true;
    document.getElementById('overlay-show-video').checked = overlay ? overlay.show_over_video : false;

    const cfg = overlay ? overlay.config : {};
    document.getElementById('overlay-text').value = cfg.text || '';
    document.getElementById('overlay-speed').value = cfg.speed || 2;
    document.getElementById('overlay-speed-label').textContent = (cfg.speed || 2) + 'x';
    document.getElementById('overlay-position').value = cfg.position || 'bottom';
    document.getElementById('overlay-custom-x').value = cfg.custom_x || '';
    document.getElementById('overlay-custom-y').value = cfg.custom_y || '';
    document.getElementById('overlay-font-size').value = cfg.font_size || 28;
    document.getElementById('overlay-text-color').value = cfg.text_color || '#FFFFFF';
    document.getElementById('overlay-bg-color').value = cfg.bg_color || '#000000';
    document.getElementById('overlay-bg-opacity').value = cfg.bg_opacity != null ? cfg.bg_opacity : 0.85;
    document.getElementById('overlay-opacity-label').textContent = Math.round((cfg.bg_opacity != null ? cfg.bg_opacity : 0.85) * 100) + '%';
    document.getElementById('overlay-image-path').value = cfg.image_path || '';
    document.getElementById('overlay-image-width').value = cfg.width || 150;
    document.getElementById('overlay-target-time').value = cfg.target_time || '';
    document.getElementById('overlay-countdown-label').value = cfg.label || 'Time remaining';
    document.getElementById('overlay-expired-text').value = cfg.expired_text || 'TIME!';
    document.getElementById('overlay-qr-url').value = cfg.url || '';
    document.getElementById('overlay-qr-label').value = cfg.label || '';
    document.getElementById('overlay-qr-size').value = cfg.size || 180;

    onOverlayTypeChange();
    onOverlayPositionChange();
}

function hideOverlayForm() {
    document.getElementById('overlay-modal').classList.add('hidden');
}

function editOverlay(overlay) {
    showOverlayForm(overlay);
}

function buildOverlayConfig() {
    const type = document.getElementById('overlay-type').value;
    const config = {
        position: document.getElementById('overlay-position').value,
    };

    if (config.position === 'custom') {
        config.custom_x = parseInt(document.getElementById('overlay-custom-x').value) || 0;
        config.custom_y = parseInt(document.getElementById('overlay-custom-y').value) || 0;
    }

    if (type === 'ticker' || type === 'static_text') {
        config.text = document.getElementById('overlay-text').value;
    }
    if (type === 'ticker') {
        config.speed = parseFloat(document.getElementById('overlay-speed').value);
    }
    if (type === 'ticker' || type === 'static_text' || type === 'countdown') {
        config.font_size = parseInt(document.getElementById('overlay-font-size').value) || 28;
        config.text_color = document.getElementById('overlay-text-color').value;
        config.bg_color = document.getElementById('overlay-bg-color').value;
        config.bg_opacity = parseFloat(document.getElementById('overlay-bg-opacity').value);
        config.padding = 12;
    }
    if (type === 'image') {
        config.image_path = document.getElementById('overlay-image-path').value;
        config.width = parseInt(document.getElementById('overlay-image-width').value) || 150;
    }
    if (type === 'countdown') {
        config.target_time = document.getElementById('overlay-target-time').value;
        config.label = document.getElementById('overlay-countdown-label').value;
        config.expired_text = document.getElementById('overlay-expired-text').value;
    }
    if (type === 'qr_code') {
        config.url = document.getElementById('overlay-qr-url').value;
        config.label = document.getElementById('overlay-qr-label').value;
        config.size = parseInt(document.getElementById('overlay-qr-size').value) || 180;
        config.padding = 10;
    }

    return config;
}

async function saveOverlay() {
    const editId = document.getElementById('overlay-edit-id').value;
    const data = {
        type: document.getElementById('overlay-type').value,
        name: document.getElementById('overlay-name').value,
        enabled: document.getElementById('overlay-enabled').checked,
        show_over_video: document.getElementById('overlay-show-video').checked,
        config: buildOverlayConfig(),
    };

    let result;
    if (editId) {
        // Update existing
        try {
            const response = await fetch(`/overlays/${editId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
            result = await response.json();
            if (response.ok) {
                log(`Overlay "${data.name}" updated.`, 'success');
            } else {
                log(`Error updating overlay: ${result.error}`, 'error');
                return;
            }
        } catch (error) {
            log(`Error updating overlay: ${error.message}`, 'error');
            return;
        }
    } else {
        // Create new
        result = await apiCall('/overlays', data);
        if (result && !result.error) {
            log(`Overlay "${data.name}" created.`, 'success');
        }
    }

    hideOverlayForm();
    await loadOverlays();
}

async function deleteOverlay(id, name) {
    if (!confirm(`Delete overlay "${name || id}"?`)) return;
    try {
        const response = await fetch(`/overlays/${id}`, { method: 'DELETE' });
        if (response.ok) {
            log(`Overlay "${name}" deleted.`, 'success');
        }
    } catch (error) {
        log(`Error deleting overlay: ${error.message}`, 'error');
    }
    await loadOverlays();
}

async function toggleOverlayEnabled(id) {
    await apiCall(`/overlays/${id}/toggle`, {});
    await loadOverlays();
}

// --- VNC Size Control ---

function setVncSize(size) {
    const el = document.getElementById('vnc-screen');
    el.classList.remove('vnc-fixed', 'vnc-fixed-400', 'vnc-fit', 'vnc-max');

    if (size === 'max') {
        el.classList.add('vnc-max');
        // Press Escape to exit max
        const handler = (e) => {
            if (e.key === 'Escape') {
                setVncSize('fit');
                document.removeEventListener('keydown', handler);
            }
        };
        document.addEventListener('keydown', handler);
    } else if (size === '200px') {
        el.classList.add('vnc-fixed');
    } else if (size === '400px') {
        el.classList.add('vnc-fixed-400');
    } else {
        el.classList.add('vnc-fit');
    }

    // Update active button
    document.querySelectorAll('.vnc-size-btn').forEach(btn => {
        btn.classList.toggle('vnc-size-active', btn.textContent.trim() === (size === 'fit' ? 'Fit' : size === 'max' ? 'Max' : size));
    });

    localStorage.setItem('kj-vnc-size', size);
}

// --- System Control (#4 dangerous action protection) ---

function dangerousAction(btn, action, label, extraWarning) {
    if (btn.dataset.armed) {
        // Second click — execute
        clearInterval(btn._confirmTimer);
        delete btn.dataset.armed;
        btn.textContent = label;
        btn.classList.remove('system-btn-armed');
        log(`System: ${label}...`);
        apiCall(`/system/${action}`, {}).then(data => {
            if (data && data.success) {
                log(data.message, 'success');
            }
        });
        return;
    }
    // First click — arm the button
    btn.dataset.armed = 'true';
    btn.classList.add('system-btn-armed');
    let remaining = 3;
    btn.textContent = `Confirm? (${remaining}s)`;
    btn._confirmTimer = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
            clearInterval(btn._confirmTimer);
            delete btn.dataset.armed;
            btn.textContent = label;
            btn.classList.remove('system-btn-armed');
        } else {
            btn.textContent = `Confirm? (${remaining}s)`;
        }
    }, 1000);
}

async function systemAction(action, label, extraWarning) {
    const message = extraWarning
        ? `${label}\n\n${extraWarning}\n\nAre you sure?`
        : `${label}\n\nAre you sure?`;
    if (!confirm(message)) return;
    log(`System: ${label}...`);
    const data = await apiCall(`/system/${action}`, {});
    if (data && data.success) {
        log(data.message, 'success');
    }
}

async function rebuildCatalog() {
    log('Rebuilding catalog...');
    const data = await apiCall('/catalog/build', {});
    if (data && data.success) {
        log(`Catalog rebuilt: ${data.count} entries indexed.`, 'success');
    }
}

function restartApp() {
    systemAction('restart-app', 'Restart KJ Controller',
        'The web UI will be briefly unavailable while the service restarts.');
}

// --- Karaoke Nerds Search ---

let knPreferredBrands = window.KJ_CONFIG.knPreferredBrands || [];
let knExpandedSongs = {};

async function searchKaraokeNerds() {
    const input = document.getElementById('kn-query');
    const btn = document.getElementById('kn-search-btn');
    const status = document.getElementById('kn-status');
    const query = input.value.trim();
    if (!query || query.length < 2) {
        log('Enter at least 2 characters to search.', 'error');
        return;
    }
    log(`Searching Karaoke Nerds: ${query}`);
    btn.disabled = true;
    status.classList.remove('hidden');
    document.getElementById('kn-stage').textContent = 'Searching karaokenerds.com...';

    const data = await apiCall('/karaoke-nerds/search', { query });

    btn.disabled = false;
    status.classList.add('hidden');
    if (data) {
        if (Array.isArray(data) && data.length === 0) {
            log('No results found on Karaoke Nerds.', 'error');
            document.getElementById('kn-results').innerHTML =
                '<div class="kn-no-results">No results found.</div>';
        } else if (data.error) {
            log(`Search error: ${data.error}`, 'error');
        } else {
            log(`Found ${data.length} song${data.length !== 1 ? 's' : ''} on Karaoke Nerds.`, 'success');
            renderKNResults(data);
        }
    }
}

function clearKNResults() {
    document.getElementById('kn-results').innerHTML = '';
    document.getElementById('kn-query').value = '';
}

function sortKNTracks(tracks) {
    const prefUpper = knPreferredBrands.map(b => b.toUpperCase());
    return [...tracks].sort((a, b) => {
        const tierA = a.is_community ? 0 : prefUpper.includes(a.brand_code.toUpperCase()) ? 1 : 2;
        const tierB = b.is_community ? 0 : prefUpper.includes(b.brand_code.toUpperCase()) ? 1 : 2;
        if (tierA !== tierB) return tierA - tierB;
        // Within preferred tier, sort by config order
        if (tierA === 1) {
            return prefUpper.indexOf(a.brand_code.toUpperCase()) - prefUpper.indexOf(b.brand_code.toUpperCase());
        }
        return a.brand_name.localeCompare(b.brand_name);
    });
}

function renderKNResults(songs) {
    const container = document.getElementById('kn-results');
    container.innerHTML = '';
    knExpandedSongs = {};

    songs.forEach((song, idx) => {
        const songId = `kn-song-${idx}`;
        const trackCount = song.tracks.length;
        const isExpanded = false;
        knExpandedSongs[songId] = isExpanded;

        // Song header
        const header = document.createElement('div');
        header.className = 'kn-song-header';
        header.onclick = () => toggleKNSong(songId);

        const chevron = document.createElement('span');
        chevron.className = 'folder-chevron' + (isExpanded ? ' expanded' : '');
        chevron.id = 'kn-chevron-' + idx;
        chevron.textContent = '\u25B6';

        const titleText = document.createElement('span');
        titleText.className = 'kn-song-title';
        titleText.textContent = `${song.title} \u2014 ${song.artist}`;

        const count = document.createElement('span');
        count.className = 'kn-track-count';
        count.textContent = `${trackCount} track${trackCount !== 1 ? 's' : ''}`;

        header.appendChild(chevron);
        header.appendChild(titleText);
        header.appendChild(count);
        container.appendChild(header);

        // Track list
        const trackList = document.createElement('div');
        trackList.className = 'kn-track-list' + (isExpanded ? '' : ' collapsed');
        trackList.id = songId;

        const sorted = sortKNTracks(song.tracks);
        sorted.forEach(track => {
            const trackEl = document.createElement('div');
            const prefUpper = knPreferredBrands.map(b => b.toUpperCase());
            const isPreferred = prefUpper.includes(track.brand_code.toUpperCase());
            trackEl.className = 'kn-track' +
                (track.is_community ? ' community' : '') +
                (isPreferred ? ' preferred' : '');

            const info = document.createElement('span');
            info.className = 'kn-track-info';

            const brandSpan = document.createElement('span');
            brandSpan.className = 'kn-brand-name';
            brandSpan.textContent = track.brand_name;
            info.appendChild(brandSpan);

            const codeSpan = document.createElement('span');
            codeSpan.className = 'kn-brand-code';
            codeSpan.textContent = track.brand_code;
            info.appendChild(codeSpan);

            if (track.is_community) {
                const badge = document.createElement('span');
                badge.className = 'kn-community-badge';
                badge.textContent = 'Community';
                info.appendChild(badge);
            } else if (isPreferred) {
                const badge = document.createElement('span');
                badge.className = 'kn-preferred-badge';
                badge.textContent = '\u2605';
                badge.title = 'Preferred brand';
                info.appendChild(badge);
            }

            const dlBtn = document.createElement('button');
            dlBtn.className = 'kn-download-btn';
            dlBtn.textContent = 'Download';
            dlBtn.onclick = (e) => {
                e.stopPropagation();
                downloadKNTrack(track.youtube_url);
            };

            trackEl.appendChild(info);
            trackEl.appendChild(dlBtn);
            trackList.appendChild(trackEl);
        });

        container.appendChild(trackList);
    });
}

function toggleKNSong(songId) {
    const el = document.getElementById(songId);
    if (!el) return;
    const isCollapsed = el.classList.toggle('collapsed');
    // Find chevron by matching index
    const idx = songId.replace('kn-song-', '');
    const chevron = document.getElementById('kn-chevron-' + idx);
    if (chevron) chevron.classList.toggle('expanded', !isCollapsed);
}

function downloadKNTrack(youtubeUrl) {
    // Inject into existing Download Song section and trigger its download flow
    const urlInput = document.getElementById('youtube-url');
    urlInput.value = youtubeUrl;
    clearKNResults();
    downloadSong();
}

function toggleKNPrefs() {
    const panel = document.getElementById('kn-prefs-panel');
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) {
        document.getElementById('kn-prefs-input').value = knPreferredBrands.join(', ');
        renderKNPrefsTags();
    }
}

function renderKNPrefsTags() {
    const container = document.getElementById('kn-prefs-tags');
    container.innerHTML = '';
    knPreferredBrands.forEach(code => {
        const tag = document.createElement('span');
        tag.className = 'kn-brand-tag';
        tag.textContent = code;
        container.appendChild(tag);
    });
}

async function saveKNPrefs() {
    const input = document.getElementById('kn-prefs-input').value;
    const brands = input.split(',').map(s => s.trim().toUpperCase()).filter(s => s);

    const data = await apiCall('/karaoke-nerds/config', { preferred_brands: brands });
    if (data && data.preferred_brands) {
        knPreferredBrands = data.preferred_brands;
        renderKNPrefsTags();
        log(`Updated preferred brands: ${knPreferredBrands.join(', ')}`, 'success');
        // Re-render results if we have them
        const results = document.getElementById('kn-results');
        if (results.children.length > 0) {
            // Trigger a fresh search to re-sort
            searchKaraokeNerds();
        }
    }
}

// --- YouTube Search ---

async function searchYouTube() {
    const input = document.getElementById('yt-query');
    const btn = document.getElementById('yt-search-btn');
    const status = document.getElementById('yt-status');
    let query = input.value.trim();
    if (!query || query.length < 2) {
        log('Enter at least 2 characters to search.', 'error');
        return;
    }
    const karaokePrefix = document.getElementById('yt-karaoke-prefix').checked;
    if (karaokePrefix) query = 'karaoke ' + query;

    log(`Searching YouTube: ${query}`);
    btn.disabled = true;
    status.classList.remove('hidden');
    document.getElementById('yt-stage').textContent = 'Searching YouTube...';

    const data = await apiCall('/youtube/search', { query });

    btn.disabled = false;
    status.classList.add('hidden');
    if (data) {
        if (Array.isArray(data) && data.length === 0) {
            log('No results found on YouTube.', 'error');
            document.getElementById('yt-results').innerHTML =
                '<div class="yt-no-results">No results found.</div>';
        } else if (data.error) {
            log(`Search error: ${data.error}`, 'error');
        } else {
            log(`Found ${data.length} result${data.length !== 1 ? 's' : ''} on YouTube.`, 'success');
            renderYTResults(data);
        }
    }
}

function clearYTResults() {
    document.getElementById('yt-results').innerHTML = '';
    document.getElementById('yt-query').value = '';
}

function renderYTResults(results) {
    const container = document.getElementById('yt-results');
    container.innerHTML = '';

    results.forEach(r => {
        const row = document.createElement('div');
        row.className = 'yt-result';

        const info = document.createElement('div');
        info.className = 'yt-result-info';

        const title = document.createElement('div');
        title.className = 'yt-result-title';
        title.textContent = r.title;
        info.appendChild(title);

        const meta = document.createElement('div');
        meta.className = 'yt-result-meta';
        const parts = [];
        if (r.channel) parts.push(r.channel);
        if (r.duration_str) parts.push(r.duration_str);
        if (r.view_count_str) parts.push(r.view_count_str + ' views');
        meta.textContent = parts.join(' · ');
        info.appendChild(meta);

        const dlBtn = document.createElement('button');
        dlBtn.className = 'yt-download-btn';
        dlBtn.textContent = 'Download';
        dlBtn.onclick = () => downloadYTTrack(r.url);

        row.appendChild(info);
        row.appendChild(dlBtn);
        container.appendChild(row);
    });
}

function downloadYTTrack(url) {
    const urlInput = document.getElementById('youtube-url');
    urlInput.value = url;
    clearYTResults();
    downloadSong();
}

function saveYTKaraokeToggle() {
    const checked = document.getElementById('yt-karaoke-prefix').checked;
    localStorage.setItem('ytKaraokePrefix', checked ? '1' : '0');
}

function loadYTKaraokeToggle() {
    const saved = localStorage.getItem('ytKaraokePrefix');
    document.getElementById('yt-karaoke-prefix').checked = saved === '1';
}

// --- Initialization ---

document.addEventListener('DOMContentLoaded', () => {
    const seekSlider = document.getElementById('seek-slider');
    seekSlider.addEventListener('mousedown', () => { isSeeking = true; });
    seekSlider.addEventListener('mouseup', () => { isSeeking = false; });
    seekSlider.addEventListener('touchstart', () => { isSeeking = true; });
    seekSlider.addEventListener('touchend', () => { isSeeking = false; });

    const searchInput = document.getElementById('catalog-search');
    searchInput.addEventListener('input', () => {
        clearTimeout(searchDebounceTimer);
        searchDebounceTimer = setTimeout(() => {
            catalogSearch(searchInput.value.trim());
        }, 300);
    });
    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') clearSearch();
    });

    // Keyboard shortcut: '/' to focus search (#8)
    document.addEventListener('keydown', (e) => {
        if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
            e.preventDefault();
            document.getElementById('catalog-search').focus();
        }
        // Escape closes overlay modal
        if (e.key === 'Escape' && !document.getElementById('overlay-modal').classList.contains('hidden')) {
            hideOverlayForm();
        }
    });

    // Overlay slider labels
    const speedSlider = document.getElementById('overlay-speed');
    if (speedSlider) {
        speedSlider.addEventListener('input', () => {
            document.getElementById('overlay-speed-label').textContent = speedSlider.value + 'x';
        });
    }
    const opacitySlider = document.getElementById('overlay-bg-opacity');
    if (opacitySlider) {
        opacitySlider.addEventListener('input', () => {
            document.getElementById('overlay-opacity-label').textContent = Math.round(opacitySlider.value * 100) + '%';
        });
    }

    // Initialize volume labels (#2)
    document.getElementById('karaoke-volume-label').textContent = volumePercent(document.getElementById('karaoke-volume').value);
    document.getElementById('filler-volume-label').textContent = volumePercent(document.getElementById('filler-volume').value);

    // Restore VNC size preference
    const savedVncSize = localStorage.getItem('kj-vnc-size');
    if (savedVncSize) setVncSize(savedVncSize);

    loadYTKaraokeToggle();
    updateStatus();
    updateMediaList();
    updateFillerMusicList();
    loadAudioDevices();
    loadDisplayResolutions();
    loadOverlays();
    checkCatalogAvailability();
    log('Nomad KJ Control initialized.');
});

setInterval(updateStatus, 2000);
