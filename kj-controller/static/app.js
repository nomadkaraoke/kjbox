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

// --- Clipboard ---

function createCopyBtn(text) {
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'Copy';
    btn.title = 'Copy name to clipboard';
    btn.onclick = (e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(text).then(() => {
            btn.textContent = 'Copied!';
            setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
        });
    };
    return btn;
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

// --- Download Queue ---

const _handledDownloads = new Set();

async function downloadSong() {
    const urlInput = document.getElementById('youtube-url');
    const url = urlInput.value;
    if (!url) {
        log('Please enter a YouTube URL.', 'error');
        return;
    }
    log(`Queuing download: ${url}`);
    const data = await apiCall('/download', { url });
    if (data && data.success) {
        urlInput.value = '';
    }
}

async function uploadFile(input) {
    const file = input.files[0];
    if (!file) return;
    const label = document.getElementById('upload-label');
    const labelText = document.getElementById('upload-label-text');
    const progress = document.getElementById('upload-progress');
    label.classList.add('uploading');
    labelText.textContent = 'Uploading: ' + file.name;
    progress.innerHTML = '<div class="dl-queue-item dl-queue-downloading"><span class="download-spinner"></span><span class="dl-queue-label">' + file.name + '</span></div>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/upload', { method: 'POST', body: formData });
        const data = await resp.json();
        if (resp.ok && data.success) {
            progress.innerHTML = '<div class="dl-queue-item dl-queue-completed"><span class="dl-queue-icon">✅</span><span class="dl-queue-label">' + (data.filename || file.name) + '</span></div>';
            log('Uploaded: ' + (data.filename || file.name));
        } else {
            progress.innerHTML = '<div class="dl-queue-item dl-queue-error"><span class="dl-queue-icon">❌</span><span class="dl-queue-label">' + (data.error || 'Upload failed') + '</span></div>';
        }
    } catch (e) {
        progress.innerHTML = '<div class="dl-queue-item dl-queue-error"><span class="dl-queue-icon">❌</span><span class="dl-queue-label">Upload failed</span></div>';
    }
    label.classList.remove('uploading');
    labelText.textContent = 'Choose file to upload...';
    input.value = '';
    setTimeout(() => { progress.innerHTML = ''; }, 5000);
}

function renderDownloadQueue(items) {
    const container = document.getElementById('download-queue');
    if (!items || items.length === 0) {
        container.innerHTML = '';
        return;
    }
    const icons = { downloading: '⏳', queued: '⏳', completed: '✅', error: '❌' };
    container.innerHTML = items.map(item => {
        const icon = icons[item.status] || '';
        const label = item.title || item.url;
        const spinner = item.status === 'downloading' ? '<span class="download-spinner"></span>' : '';
        let action = '';
        if (item.status === 'queued') {
            action = `<button class="dl-queue-action" onclick="cancelQueueItem('${item.id}')">Cancel</button>`;
        } else if (item.status === 'error') {
            action = `<button class="dl-queue-action" onclick="ackQueueItem('${item.id}')">Dismiss</button>`;
        }
        return `<div class="dl-queue-item dl-queue-${item.status}">${spinner}<span class="dl-queue-icon">${icon}</span><span class="dl-queue-label" title="${item.url}">${label}</span>${action}</div>`;
    }).join('');
}

async function handleDownloadQueue(items) {
    if (!items) return;

    renderDownloadQueue(items);

    for (const item of items) {
        if (item.status === 'completed' && !_handledDownloads.has(item.id)) {
            _handledDownloads.add(item.id);
            log(`Downloaded "${item.title}" successfully!`, 'success');

            await refreshMediaData();
            if (searchActive) clearSearch();
            else renderFolderView(applyMediaFilter(localMediaItems));
            expandDownloadsFolder();

            // Auto-dismiss after 3s
            setTimeout(() => ackQueueItem(item.id), 3000);
        }
        if (item.status === 'error' && !_handledDownloads.has(item.id)) {
            _handledDownloads.add(item.id);
            log(`Download failed: ${item.error || 'Unknown error'}`, 'error');
        }
    }
}

async function cancelQueueItem(id) {
    await apiCall('/download/cancel', { id });
}

async function ackQueueItem(id) {
    await fetch('/download/ack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
    });
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
        await refreshMediaData();
        if (searchActive) {
            const query = document.getElementById('catalog-search').value.trim();
            if (query) catalogSearch(query);
        } else {
            renderFolderView(applyMediaFilter(localMediaItems));
        }
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

const _volumeTimers = {};

function debouncedSetVolume(target, level) {
    if (_volumeTimers[target]) clearTimeout(_volumeTimers[target]);
    _volumeTimers[target] = setTimeout(() => setVolume(target, level), 150);
}

function updateKaraokeVolume(value) {
    document.getElementById('karaoke-volume-label').textContent = volumePercent(value);
    debouncedSetVolume('karaoke', value);
}

function updateFillerVolume(value) {
    document.getElementById('filler-volume-label').textContent = volumePercent(value);
    debouncedSetVolume('filler', value);
}

async function setVolume(target, level) {
    await apiCall('/volume', { target, level: parseInt(level) });
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
// Media format filter: 'all' | 'mp4' | 'cdg'
const MEDIA_FILTERS = ['all', 'mp4', 'cdg'];
const MEDIA_FILTER_LABELS = { all: 'All Formats', mp4: 'MP4 Only', cdg: 'CDG/ZIP Only' };
let mediaFilter = localStorage.getItem('kj-media-filter') || 'all';
// Migrate old boolean setting
if (mediaFilter === 'true' || localStorage.getItem('kj-mp4-only') === 'true') {
    mediaFilter = 'mp4';
    localStorage.setItem('kj-media-filter', mediaFilter);
    localStorage.removeItem('kj-mp4-only');
}

function applyMediaFilter(items) {
    if (mediaFilter === 'all') return items;
    return items.filter(item => {
        const ext = item.filename.split('.').pop().toLowerCase();
        const isCdgZip = ext === 'zip' || ext === 'cdg';
        return mediaFilter === 'mp4' ? !isCdgZip : isCdgZip;
    });
}

function cycleMediaFilter() {
    const idx = MEDIA_FILTERS.indexOf(mediaFilter);
    mediaFilter = MEDIA_FILTERS[(idx + 1) % MEDIA_FILTERS.length];
    localStorage.setItem('kj-media-filter', mediaFilter);
    updateMediaFilterBtn();
    if (searchActive) {
        const query = document.getElementById('catalog-search').value.trim();
        if (query) catalogSearch(query);
    } else {
        renderFolderView(applyMediaFilter(localMediaItems));
    }
}

function updateMediaFilterBtn() {
    const btn = document.getElementById('media-filter-btn');
    if (!btn) return;
    btn.textContent = MEDIA_FILTER_LABELS[mediaFilter];
    btn.classList.toggle('active', mediaFilter !== 'all');
}

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

function expandDownloadsFolder() {
    // Find the downloads folder by checking is_download items
    const dlItem = localMediaItems.find(i => i.is_download);
    if (!dlItem) return;
    const folderName = dlItem.folder_name;
    const escapedName = CSS.escape(folderName);
    const container = document.getElementById('folder-' + escapedName);
    const chevron = document.getElementById('chevron-' + escapedName);
    if (container) container.classList.remove('collapsed');
    if (chevron) chevron.classList.add('expanded');
    const states = loadFolderStates();
    states[folderName] = true;
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
    rightSide.appendChild(createCopyBtn(item.display_name));
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

    // Sort folder names alphabetically, but pin the download folder to the top
    const downloadFolderName = items.find(i => i.is_download)?.folder_name;
    const folderNames = Object.keys(groups).sort((a, b) => {
        if (a === downloadFolderName) return -1;
        if (b === downloadFolderName) return 1;
        return a.localeCompare(b);
    });
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

async function refreshMediaData() {
    const response = await fetch('/media');
    localMediaItems = await response.json();
}

async function updateMediaList() {
    if (searchActive) return;
    try {
        await refreshMediaData();
        renderFolderView(applyMediaFilter(localMediaItems));
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

            // Sync volume sliders from server (don't fight active drag)
            if (data.karaoke_volume !== undefined) {
                const kSlider = document.getElementById('karaoke-volume');
                if (document.activeElement !== kSlider) {
                    kSlider.value = data.karaoke_volume;
                    document.getElementById('karaoke-volume-label').textContent = volumePercent(data.karaoke_volume);
                }
            }
            if (data.filler_volume !== undefined) {
                const fSlider = document.getElementById('filler-volume');
                if (document.activeElement !== fSlider) {
                    fSlider.value = data.filler_volume;
                    document.getElementById('filler-volume-label').textContent = volumePercent(data.filler_volume);
                }
            }

            // Track download queue progress
            await handleDownloadQueue(data.download_queue);

            // Check rotation downloads for completion
            if (data.rotation_downloads) {
                let needsRotRefresh = false;
                for (const [entryId, dl] of Object.entries(data.rotation_downloads)) {
                    if (dl.status === 'completed') needsRotRefresh = true;
                }
                if (needsRotRefresh) fetchRotation();
            }

            // Poll gen job statuses periodically (every 30s via counter)
            if (!window._genPollCounter) window._genPollCounter = 0;
            window._genPollCounter++;
            if (window._genPollCounter >= 15) {  // 15 * 2s = 30s
                window._genPollCounter = 0;
                pollGenStatuses();
            }

            // Browser mode status
            updateBrowserModeUI(data.browser_mode);
        }
    } catch (error) {
        // Don't log periodic status check errors to avoid clutter
    }
}

async function pollGenStatuses() {
    try {
        const resp = await fetch('/rotation/gen-status');
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.gen_entries && data.gen_entries.length > 0) {
            // Refresh rotation to pick up badge changes
            fetchRotation();
        }
    } catch (e) {
        // Best-effort
    }
}

// --- AV Output Modal ---

async function openAvModal() {
    document.getElementById('av-modal').classList.remove('hidden');
    await avRefresh();
}

function closeAvModal() {
    document.getElementById('av-modal').classList.add('hidden');
}

async function avRefresh() {
    document.getElementById('av-loading').classList.remove('hidden');
    document.getElementById('av-content').classList.add('hidden');
    try {
        const response = await fetch('/av/status');
        if (!response.ok) throw new Error('AV status fetch failed');
        const data = await response.json();
        renderAvModal(data);
    } catch (e) {
        const errEl = document.getElementById('av-loading');
        errEl.textContent = '';
        const span = document.createElement('span');
        span.style.color = '#ef4444';
        span.textContent = `Error loading AV status: ${e.message}`;
        errEl.appendChild(span);
    }
}

function renderAvModal(data) {
    renderAvHealthBar(data.health);
    renderAvVideoSection(data.video);
    renderAvAudioSection(data.audio);
    renderAvBrowserAudioSection(data.audio);
    populateAvResolutionSelect(data.video);
    populateAvHdmiPcmSelect(data.audio);
    populateAvVlcDeviceSelect(data.audio);
    populateAvBrowserAudioSelect(data.audio);
    document.getElementById('av-loading').classList.add('hidden');
    document.getElementById('av-content').classList.remove('hidden');
}

function avDot(cls) {
    return `<span class="av-dot ${cls}"></span>`;
}

function renderAvHealthBar(health) {
    const items = [
        { key: 'video_ok',              label: 'Video' },
        { key: 'audio_ok',              label: 'Audio' },
        { key: 'asound_matches_active_jack', label: 'ALSA jack' },
        { key: 'iec958_ok',             label: 'IEC958' },
        { key: 'pipewire_profile_ok',   label: 'PipeWire' },
    ];
    const bar = document.getElementById('av-health-bar');
    bar.innerHTML = items.map(({ key, label }) => {
        const ok = health[key];
        const cls = ok ? 'av-health-ok' : 'av-health-error';
        const dotCls = ok ? 'av-dot-ok' : 'av-dot-error';
        return `<span class="av-health-item ${cls}">${avDot(dotCls)} ${label}</span>`;
    }).join('');
}

function renderAvVideoSection(video) {
    const list = document.getElementById('av-video-connectors');
    list.innerHTML = '';
    const connectors = video.connectors || {};
    if (Object.keys(connectors).length === 0) {
        list.innerHTML = '<div style="color:#555;font-size:0.82em;">No display connectors found (xrandr not available)</div>';
        return;
    }
    for (const [name, info] of Object.entries(connectors)) {
        const div = document.createElement('div');
        div.className = 'av-connector' + (info.connected ? '' : ' av-connector-disconnected');

        const dotCls = info.connected ? (name === video.active_output ? 'av-dot-ok' : 'av-dot-warn') : 'av-dot-off';
        let html = `${avDot(dotCls)} <span class="av-connector-name">${escapeHtml(name)}</span>`;

        if (info.connected) {
            if (info.current_resolution) {
                html += ` <span class="av-connector-resolution">${escapeHtml(info.current_resolution)}</span>`;
                if (info.current_refresh) {
                    html += ` <span class="av-connector-refresh">@ ${escapeHtml(info.current_refresh)}Hz</span>`;
                }
            } else {
                html += ` <span style="color:#555;font-size:0.85em;">(no mode set)</span>`;
            }
            if (name === video.active_output) {
                html += ` <span class="av-connector-active-badge">active</span>`;
            }
            if (info.edid_name) {
                html += ` <span class="av-connector-edid" title="Monitor name from EDID">${escapeHtml(info.edid_name)}</span>`;
            }
        } else {
            html += ` <span style="color:#444;font-size:0.82em;">disconnected</span>`;
        }

        div.innerHTML = html;
        list.appendChild(div);
    }
}

function renderAvAudioSection(audio) {
    const infoEl = document.getElementById('av-audio-info');
    const vlcDotCls = audio.vlc_device === 'hdmiout' || audio.vlc_device === audio.asound_hw ? 'av-dot-ok' : 'av-dot-warn';
    const asDotCls = audio.asound_hw ? 'av-dot-ok' : 'av-dot-warn';
    const pwDotCls = audio.pipewire_ok ? 'av-dot-ok' : 'av-dot-error';

    let html = `
        <div class="av-info-row">
            <span class="av-info-label">VLC device</span>
            ${avDot(vlcDotCls)}
            <span class="av-info-value">${escapeHtml(audio.vlc_device || '—')}</span>
        </div>
        <div class="av-info-row">
            <span class="av-info-label">hdmiout alias</span>
            ${avDot(asDotCls)}
            <span class="av-info-value">${escapeHtml(audio.asound_hw || '(not set)')}</span>
        </div>`;

    if (audio.eld_names && audio.eld_names.length > 0) {
        html += `<div class="av-eld-names">Connected display audio: ${audio.eld_names.map(escapeHtml).join(', ')}</div>`;
    }

    html += `
        <div class="av-info-row" style="margin-top:4px;">
            <span class="av-info-label">PipeWire profile</span>
            ${avDot(pwDotCls)}
            <span class="av-info-value" style="font-size:0.8em;">${escapeHtml(audio.pipewire_profile || '(unknown)')}</span>
        </div>`;

    if (!audio.pipewire_ok && audio.pipewire_profile) {
        html += `<div class="av-info-warn">PipeWire may be locking the HDMI device. Reset All will fix this.</div>`;
    }

    infoEl.innerHTML = html;

    // PCM table
    const tbody = document.getElementById('av-pcm-tbody');
    tbody.innerHTML = '';
    for (const [hwId, info] of Object.entries(audio.hdmi_pcms || {})) {
        const isAlias = hwId === audio.asound_hw;
        const tr = document.createElement('tr');
        if (isAlias) tr.className = 'av-pcm-active';

        const jackDot = info.connected ? avDot('av-dot-ok') : avDot('av-dot-off');
        const iec958Dot = info.iec958 ? avDot('av-dot-ok') : avDot('av-dot-error');

        tr.innerHTML = `
            <td>${escapeHtml(hwId)}${isAlias ? '<span class="av-alias-badge">hdmiout</span>' : ''}</td>
            <td>${escapeHtml(info.name)}</td>
            <td>${jackDot} ${info.connected ? 'on' : 'off'}</td>
            <td>${iec958Dot} ${info.iec958 ? 'on' : 'off'}</td>`;
        tbody.appendChild(tr);
    }
}

function populateAvResolutionSelect(video) {
    const sel = document.getElementById('av-resolution-select');
    sel.innerHTML = '';
    const activeConn = video.connectors && video.active_output
        ? video.connectors[video.active_output]
        : null;
    const modes = activeConn ? activeConn.available_modes : [];
    const current = activeConn ? activeConn.current_resolution : null;

    if (modes.length === 0) {
        sel.innerHTML = '<option disabled>N/A</option>';
        return;
    }
    modes.forEach(mode => {
        const opt = document.createElement('option');
        opt.value = mode;
        opt.textContent = mode;
        if (mode === current) opt.selected = true;
        sel.appendChild(opt);
    });
}

function populateAvHdmiPcmSelect(audio) {
    const sel = document.getElementById('av-hdmi-pcm-select');
    sel.innerHTML = '';
    for (const [hwId, info] of Object.entries(audio.hdmi_pcms || {})) {
        const opt = document.createElement('option');
        opt.value = hwId;
        const jackLabel = info.connected ? ' [connected]' : '';
        opt.textContent = `${hwId} — ${info.name}${jackLabel}`;
        if (hwId === audio.asound_hw) opt.selected = true;
        sel.appendChild(opt);
    }
}

function populateAvVlcDeviceSelect(audio) {
    const sel = document.getElementById('av-vlc-device-select');
    sel.innerHTML = '';

    const devices = [
        { value: 'hdmiout', label: 'hdmiout — ALSA alias (normal operation)' },
    ];
    // Add individual HDMI PCM devices
    for (const [hwId, info] of Object.entries(audio.hdmi_pcms || {})) {
        const jackLabel = info.connected ? ' [connected]' : '';
        devices.push({ value: hwId, label: `${hwId} — ${info.name}${jackLabel}` });
    }
    // Analog / 3.5mm jack
    devices.push({ value: 'hw:0,0', label: 'hw:0,0 — Analog / 3.5mm jack' });
    // USB mixer if configured
    devices.push({ value: 'usbmixer', label: 'usbmixer — USB Mixer' });

    devices.forEach(({ value, label }) => {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = label;
        if (value === audio.vlc_device) opt.selected = true;
        sel.appendChild(opt);
    });
}

function renderAvBrowserAudioSection(audio) {
    const infoEl = document.getElementById('av-browser-audio-info');
    const ba = audio.browser_audio || {};
    const isSame = ba.setting === 'same';
    const dotCls = 'av-dot-ok';

    let label;
    if (isSame) {
        label = `Same as VLC → ${ba.resolved_profile || '(unknown)'}`;
    } else {
        label = ba.setting || '(unknown)';
    }

    infoEl.innerHTML = `
        <div class="av-info-row">
            <span class="av-info-label">Browser device</span>
            ${avDot(dotCls)}
            <span class="av-info-value" style="font-size:0.8em;">${escapeHtml(label)}</span>
        </div>`;
}

function populateAvBrowserAudioSelect(audio) {
    const sel = document.getElementById('av-browser-audio-select');
    sel.innerHTML = '';
    const ba = audio.browser_audio || {};
    const currentSetting = ba.setting || 'same';

    // "Same as VLC" option
    const sameOpt = document.createElement('option');
    sameOpt.value = 'same';
    sameOpt.textContent = `Same as VLC (${ba.resolved_profile || 'auto'})`;
    if (currentSetting === 'same') sameOpt.selected = true;
    sel.appendChild(sameOpt);

    // Individual PipeWire profiles
    const profiles = ba.available_profiles || {};
    const profileLabels = {
        'hdmi': 'HDMI — audio via HDMI display/splitter',
        'analog': 'Analog — 3.5mm headphone jack',
    };
    for (const [key, profileStr] of Object.entries(profiles)) {
        const opt = document.createElement('option');
        opt.value = profileStr;
        opt.textContent = profileLabels[key] || `${key} — ${profileStr}`;
        if (currentSetting === profileStr) opt.selected = true;
        sel.appendChild(opt);
    }
}

async function avSetBrowserAudio(device) {
    if (!device) return;
    log(`Setting browser audio to ${device === 'same' ? 'Same as VLC' : device}...`);
    const data = await apiCall('/av/browser-audio', { device });
    if (data && data.success) {
        log(`Browser audio set to ${data.device}`, 'success');
        setTimeout(avRefresh, 500);
    }
}

async function avSetResolution(resolution) {
    if (!resolution) return;
    log(`Setting display resolution to ${resolution}...`);
    const data = await apiCall('/display/resolution', { resolution });
    if (data && data.success) {
        log(data.message, 'success');
        setTimeout(avRefresh, 1000);
    }
}

async function avSwitchHdmiPcm(device) {
    if (!device) return;
    log(`Switching hdmiout alias to ${device}...`);
    const data = await apiCall('/audio/switch-hdmi', { device });
    if (data && data.success) {
        log(data.message, 'success');
        setTimeout(avRefresh, 3500);
    }
}

async function avSetVlcDevice(device) {
    if (!device) return;
    log(`Switching VLC audio device to ${device}...`);
    const data = await apiCall('/av/vlc-device', { device });
    if (data && data.success) {
        log(data.message, 'success');
        setTimeout(avRefresh, 3500);
    }
}

async function avReset() {
    const btn = document.getElementById('av-reset-btn');
    btn.disabled = true;
    btn.textContent = 'Resetting…';
    log('Running AV reset (fix-hdmi-audio.sh + display + PipeWire + VLC)…');
    const data = await apiCall('/av/reset', {});
    btn.disabled = false;
    btn.textContent = 'Reset All to Known-Good State';
    if (data && data.success) {
        log('AV reset complete.', 'success');
        setTimeout(avRefresh, 4500);
    }
}

// --- YouTube Settings Modal ---

async function openYtModal() {
    document.getElementById('yt-modal').classList.remove('hidden');
    await ytSettingsRefresh();
}

function closeYtModal() {
    document.getElementById('yt-modal').classList.add('hidden');
}

async function ytSettingsRefresh() {
    document.getElementById('yt-loading').classList.remove('hidden');
    document.getElementById('yt-content').classList.add('hidden');
    try {
        const response = await fetch('/youtube/status');
        if (!response.ok) throw new Error('YouTube status fetch failed');
        const data = await response.json();
        renderYtSettings(data);
        updateYtHealthDot(data);
    } catch (e) {
        const errEl = document.getElementById('yt-loading');
        errEl.textContent = '';
        const span = document.createElement('span');
        span.style.color = '#ef4444';
        span.textContent = `Error loading YouTube status: ${e.message}`;
        errEl.appendChild(span);
    }
}

function renderYtSettings(data) {
    // Health bar
    const bar = document.getElementById('yt-health-bar');
    const ytdlpOk = !!data.ytdlp_version && !data.ytdlp_outdated;
    const items = [
        { label: 'yt-dlp', ok: ytdlpOk, warn: !!data.ytdlp_version && !ytdlpOk },
        { label: 'EJS', ok: data.ejs_installed },
        { label: 'Deno', ok: data.deno_available },
        { label: 'Cookies', ok: data.cookies_present && data.cookies_valid },
    ];
    bar.innerHTML = items.map(({ label, ok, warn }) => {
        const cls = ok ? 'av-health-ok' : (warn || label === 'Cookies' ? 'av-health-warn' : 'av-health-error');
        const dotCls = ok ? 'av-dot-ok' : (warn || label === 'Cookies' ? 'av-dot-warn' : 'av-dot-error');
        return `<span class="av-health-item ${cls}">${avDot(dotCls)} ${label}</span>`;
    }).join('');

    // Engine info
    const engineEl = document.getElementById('yt-engine-info');
    const ytdlpOutdated = data.ytdlp_outdated;
    const ytdlpDotCls = !data.ytdlp_version ? 'av-dot-error' : (ytdlpOutdated ? 'av-dot-warn' : 'av-dot-ok');
    const ytdlpExtra = ytdlpOutdated
        ? ` <span class="yt-outdated">(latest: ${escapeHtml(data.ytdlp_latest)})</span> <button class="system-btn yt-upgrade-btn" onclick="upgradeYtdlp(this)">Update</button>`
        : '';
    engineEl.innerHTML = `
        <div class="av-info-row">
            <span class="av-info-label">yt-dlp</span>
            ${avDot(ytdlpDotCls)}
            <span class="av-info-value">${escapeHtml(data.ytdlp_version || 'not installed')}${ytdlpExtra}</span>
        </div>
        <div class="av-info-row">
            <span class="av-info-label">EJS solver</span>
            ${avDot(data.ejs_installed ? 'av-dot-ok' : 'av-dot-warn')}
            <span class="av-info-value">${data.ejs_installed ? escapeHtml(data.ejs_version || 'installed') : 'not installed'}</span>
        </div>
        <div class="av-info-row">
            <span class="av-info-label">Deno runtime</span>
            ${avDot(data.deno_available ? 'av-dot-ok' : 'av-dot-warn')}
            <span class="av-info-value">${data.deno_available ? escapeHtml(data.deno_version) : 'not installed'}</span>
        </div>`;

    // Cookie status
    const cookieEl = document.getElementById('yt-cookie-status');
    if (data.cookies_present) {
        const validDot = data.cookies_valid ? 'av-dot-ok' : 'av-dot-error';
        const validLabel = data.cookies_valid ? 'valid' : 'invalid format';
        let updated = '';
        if (data.cookies_last_updated) {
            const d = new Date(data.cookies_last_updated * 1000);
            updated = ` (updated ${d.toLocaleDateString()} ${d.toLocaleTimeString()})`;
        }
        cookieEl.innerHTML = `
            <div class="av-info-row">
                <span class="av-info-label">Status</span>
                ${avDot(validDot)}
                <span class="av-info-value">${escapeHtml(validLabel)}${escapeHtml(updated)}</span>
            </div>`;
    } else {
        cookieEl.innerHTML = `
            <div class="av-info-row">
                <span class="av-info-label">Status</span>
                ${avDot('av-dot-warn')}
                <span class="av-info-value">no cookies file</span>
            </div>`;
    }

    document.getElementById('yt-loading').classList.add('hidden');
    document.getElementById('yt-content').classList.remove('hidden');
}

function updateYtHealthDot(data) {
    const dot = document.getElementById('yt-health-dot');
    if (!dot) return;
    // Green: yt-dlp works + cookies present
    // Yellow: yt-dlp works but missing EJS or cookies
    // Red: yt-dlp broken
    dot.className = 'yt-health-dot';
    if (!data.ytdlp_version) {
        dot.classList.add('yt-dot-error');
    } else if (!data.ejs_installed || !data.cookies_present || !data.cookies_valid
               || data.ytdlp_outdated) {
        dot.classList.add('yt-dot-warn');
    } else {
        dot.classList.add('yt-dot-ok');
    }
}

async function uploadYtCookies() {
    const textarea = document.getElementById('yt-cookie-textarea');
    const content = textarea.value.trim();
    if (!content) {
        log('Paste cookie content first.', 'error');
        return;
    }
    const btn = document.getElementById('yt-upload-btn');
    btn.disabled = true;
    btn.textContent = 'Uploading…';
    try {
        const data = await apiCall('/youtube/cookies', { content });
        if (data && data.success) {
            log('YouTube cookies uploaded: ' + data.message, 'success');
            textarea.value = '';
            await ytSettingsRefresh();
        }
    } finally {
        btn.disabled = false;
        btn.textContent = 'Upload Cookies';
    }
}

async function deleteYtCookies() {
    if (!confirm('Delete YouTube cookies? Downloads may start failing if rate-limited.')) return;
    try {
        const resp = await fetch('/youtube/cookies', { method: 'DELETE' });
        const data = await resp.json();
        if (data.success) {
            log('YouTube cookies deleted.', 'success');
            await ytSettingsRefresh();
        } else {
            log('Error: ' + (data.error || 'Unknown'), 'error');
        }
    } catch (e) {
        log('Error deleting cookies: ' + e.message, 'error');
    }
}

async function upgradeYtdlp(btn) {
    if (!confirm('Update yt-dlp to the latest version?\n\nThis will restart the service.')) return;
    btn.disabled = true;
    btn.textContent = 'Updating...';

    // Show restart overlay in modal
    const contentEl = document.getElementById('yt-content');
    const loadingEl = document.getElementById('yt-loading');
    function showOverlay(msg) {
        contentEl.classList.add('hidden');
        loadingEl.innerHTML = `<span class="download-spinner"></span> ${escapeHtml(msg)}`;
        loadingEl.classList.remove('hidden');
    }

    try {
        showOverlay('Upgrading yt-dlp...');
        const response = await fetch('/youtube/upgrade-ytdlp', { method: 'POST' });
        const data = await response.json();
        if (!response.ok) {
            log('yt-dlp upgrade failed: ' + (data.error || 'Unknown error'), 'error');
            await ytSettingsRefresh();
            return;
        }
        log(data.message, 'success');
        if (data.restarting) {
            showOverlay('Restarting service, please wait...');
            await waitForRestart();
            log('Service restarted.', 'success');
        }
        await ytSettingsRefresh();
    } catch (e) {
        log('yt-dlp upgrade failed: ' + e.message, 'error');
        await ytSettingsRefresh();
    }
}

async function waitForRestart(maxSeconds = 30) {
    // Wait a moment for the service to actually stop
    await new Promise(r => setTimeout(r, 2000));
    // Poll until backend responds again
    for (let i = 0; i < maxSeconds; i++) {
        try {
            const r = await fetch('/status', { signal: AbortSignal.timeout(2000) });
            if (r.ok) return;
        } catch (e) { /* still restarting */ }
        await new Promise(r => setTimeout(r, 1000));
    }
}

// Fetch YouTube health dot on page load
fetch('/youtube/status').then(r => r.json()).then(updateYtHealthDot).catch(() => {});

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
    return applyMediaFilter(localMediaItems).filter(item => {
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
            li.appendChild(createCopyBtn(nameText));
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
    renderFolderView(applyMediaFilter(localMediaItems));
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

async function backupOverlays() {
    try {
        const response = await fetch('/overlays');
        const overlays = await response.json();
        const blob = new Blob([JSON.stringify(overlays, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'overlays-backup.json';
        a.click();
        URL.revokeObjectURL(url);
        log('Overlay config downloaded.', 'success');
    } catch (e) {
        log('Failed to backup overlays: ' + e.message, 'error');
    }
}

function restoreOverlays() {
    document.getElementById('overlay-restore-input').click();
}

async function handleOverlayRestore(input) {
    const file = input.files[0];
    input.value = '';
    if (!file) return;
    try {
        const text = await file.text();
        const data = JSON.parse(text);
        if (!Array.isArray(data)) {
            log('Invalid overlay backup file — expected a JSON array.', 'error');
            return;
        }
        if (!confirm(`Restore ${data.length} overlay(s)? This will replace all current overlays.`)) return;
        const response = await fetch('/overlays/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const result = await response.json();
        if (!response.ok) {
            log('Restore failed: ' + (result.error || 'Unknown error'), 'error');
            return;
        }
        log(`Restored ${result.count} overlay(s).`, 'success');
        await loadOverlays();
    } catch (e) {
        log('Failed to restore overlays: ' + e.message, 'error');
    }
}

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

function hideVncPreview() {
    // Mark hidden first so disconnect event handlers don't re-show elements
    const container = document.getElementById('vnc-preview-container');
    container.dataset.hidden = 'true';
    localStorage.setItem('kj-vnc-hidden', '1');
    // Disconnect without forgetting password
    if (window.disconnectVnc) window.disconnectVnc();
    // Collapse everything
    container.querySelectorAll('#vnc-screen, #vnc-status, .vnc-password-form, #vnc-controls, #vnc-interactive-controls').forEach(
        el => el.classList.add('hidden')
    );
    document.getElementById('vnc-max-toolbar').classList.remove('visible');
    document.querySelectorAll('.vnc-size-btn').forEach(btn => btn.classList.remove('vnc-size-active'));
    document.querySelector('.vnc-hide-btn').classList.add('vnc-size-active');
}

function showVncPreview() {
    const container = document.getElementById('vnc-preview-container');
    container.querySelectorAll('#vnc-screen, #vnc-status').forEach(
        el => el.classList.remove('hidden')
    );
    // Show password form or controls depending on whether we have a saved password
    const hasPw = !!localStorage.getItem('kj-vnc-password');
    document.getElementById('vnc-password-form').classList.toggle('hidden', hasPw);
    document.getElementById('vnc-controls').classList.toggle('hidden', !hasPw);
    delete container.dataset.hidden;
    document.querySelector('.vnc-hide-btn').classList.remove('vnc-size-active');
    localStorage.removeItem('kj-vnc-hidden');
    // Reconnect if we have a saved password
    if (hasPw && window.connectVnc) window.connectVnc(localStorage.getItem('kj-vnc-password'));
}

function setVncSize(size) {
    // Un-hide if hidden
    const container = document.getElementById('vnc-preview-container');
    if (container.dataset.hidden) showVncPreview();

    const el = document.getElementById('vnc-screen');
    el.classList.remove('vnc-fixed', 'vnc-fixed-400', 'vnc-fit', 'vnc-max');

    const maxToolbar = document.getElementById('vnc-max-toolbar');

    if (size === 'max') {
        el.classList.add('vnc-max');
        maxToolbar.classList.add('visible');
    } else {
        maxToolbar.classList.remove('visible');
        if (size === '200px') {
            el.classList.add('vnc-fixed');
        } else if (size === '400px') {
            el.classList.add('vnc-fixed-400');
        } else {
            el.classList.add('vnc-fit');
        }
    }

    // Update active button (both inline and max toolbar buttons)
    document.querySelectorAll('.vnc-size-btn').forEach(btn => {
        btn.classList.toggle('vnc-size-active', btn.textContent.trim() === (size === 'fit' ? 'Fit' : size === 'max' ? 'Max' : size));
    });

    localStorage.setItem('kj-vnc-size', size);
}

// --- System Control (#4 dangerous action protection) ---

function showSystemOverlay(msg) {
    const el = document.getElementById('system-overlay');
    const msgEl = document.getElementById('system-overlay-msg');
    el.classList.remove('hidden', 'success');
    el.querySelector('.download-spinner').style.display = '';
    msgEl.textContent = msg;
}

function showSystemSuccess(msg) {
    const el = document.getElementById('system-overlay');
    const msgEl = document.getElementById('system-overlay-msg');
    el.classList.remove('hidden');
    el.classList.add('success');
    el.querySelector('.download-spinner').style.display = 'none';
    msgEl.textContent = msg;
    setTimeout(() => el.classList.add('hidden'), 4000);
}

function hideSystemOverlay() {
    document.getElementById('system-overlay').classList.add('hidden');
}

function dangerousAction(btn, action, label, extraWarning) {
    if (btn.dataset.armed) {
        // Second click — execute
        clearInterval(btn._confirmTimer);
        delete btn.dataset.armed;
        btn.textContent = label;
        btn.classList.remove('system-btn-armed');
        executeDangerousAction(action, label);
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

async function executeDangerousAction(action, label) {
    showSystemOverlay(`${label}ing...`);
    const data = await apiCall(`/system/${action}`, {});
    if (data && data.success) {
        if (action === 'shutdown') {
            showSystemOverlay('System is shutting down...');
            return;
        }
        // Reboot — wait for it to come back
        showSystemOverlay('Waiting for system to come back online...');
        await waitForRestart(60);
        showSystemSuccess('System is back online.');
    } else {
        hideSystemOverlay();
    }
}

async function fetchAutoDeployStatus() {
    try {
        const response = await fetch('/system/autodeploy');
        const data = await response.json();
        document.getElementById('autodeploy-switch').checked = data.active;
    } catch (e) { /* ignore */ }
}

async function toggleAutoDeploy(active) {
    try {
        const response = await fetch('/system/autodeploy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active })
        });
        const data = await response.json();
        document.getElementById('autodeploy-switch').checked = data.active;
        log(`Auto-deploy ${data.active ? 'enabled' : 'disabled'}`, 'success');
    } catch (e) {
        log('Failed to toggle auto-deploy', 'error');
        fetchAutoDeployStatus(); // revert switch to actual state
    }
}

// --- Sleep Mode ---

let _sleepModeActive = false;

async function fetchSleepModeStatus() {
    try {
        const response = await fetch('/system/sleep-mode');
        const data = await response.json();
        _sleepModeActive = data.active;
        const sw = document.getElementById('sleep-mode-switch');
        if (sw) sw.checked = data.active;
        updateSleepModeUI(data);
    } catch (e) { /* ignore */ }
}

function updateSleepModeUI(data) {
    const statusEl = document.getElementById('sleep-mode-status');
    const section = document.getElementById('sleep-mode-section');
    if (!statusEl || !section) return;

    if (data.active) {
        section.classList.add('sleep-mode-active');
        const entered = data.state && data.state.entered_at
            ? new Date(data.state.entered_at).toLocaleString()
            : 'unknown';
        statusEl.textContent = `Sleeping since ${entered}. Services stopped, SSD unmounted, power-saver mode.`;
        statusEl.classList.remove('hidden');
    } else if (data.entering) {
        section.classList.add('sleep-mode-active');
        statusEl.textContent = 'Entering sleep mode...';
        statusEl.classList.remove('hidden');
    } else if (data.exiting) {
        section.classList.remove('sleep-mode-active');
        statusEl.textContent = 'Waking up...';
        statusEl.classList.remove('hidden');
    } else {
        section.classList.remove('sleep-mode-active');
        statusEl.classList.add('hidden');
    }
}

async function toggleSleepMode(active) {
    const sw = document.getElementById('sleep-mode-switch');

    if (active) {
        // Entering sleep — confirm first
        const ok = confirm(
            'Enter Sleep Mode?\n\n' +
            'This will:\n' +
            '- Stop VLC, overlays, rotation display, VNC\n' +
            '- Unmount and power down the USB SSD\n' +
            '- Switch to power-saver mode\n' +
            '- Stop Dropbox and other services\n\n' +
            'The web UI will remain accessible to wake the system.'
        );
        if (!ok) {
            if (sw) sw.checked = false;
            return;
        }
    }

    const statusEl = document.getElementById('sleep-mode-status');
    if (statusEl) {
        statusEl.textContent = active ? 'Entering sleep mode...' : 'Waking up...';
        statusEl.classList.remove('hidden');
    }

    try {
        const response = await fetch('/system/sleep-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active })
        });
        const data = await response.json();
        _sleepModeActive = data.active;
        if (sw) sw.checked = data.active;

        if (data.active) {
            log('Sleep mode activated', 'success');
            updateSleepModeUI(data);
        } else {
            log('System awake', 'success');
            updateSleepModeUI({ active: false });
        }

        if (data.errors && data.errors.length > 0) {
            log(`Sleep mode warnings: ${data.errors.join('; ')}`, 'warn');
        }
    } catch (e) {
        log('Failed to toggle sleep mode', 'error');
        fetchSleepModeStatus(); // revert switch to actual state
    }
}

async function rebuildCatalog() {
    log('Rebuilding catalog...');
    const data = await apiCall('/catalog/build', {});
    if (data && data.success) {
        log(`Catalog rebuilt: ${data.count} entries indexed.`, 'success');
    }
}

async function restartApp() {
    if (!confirm('Restart KJ Controller?\n\nThe web UI will be briefly unavailable while the service restarts.')) return;
    showSystemOverlay('Restarting service...');
    const data = await apiCall('/system/restart-app', {});
    if (data && data.success) {
        showSystemOverlay('Waiting for service to restart...');
        await waitForRestart();
        showSystemSuccess('Service restarted successfully.');
    } else {
        hideSystemOverlay();
    }
}

async function updateApp() {
    if (!confirm('Update & Restart KJ Controller?\n\nPulls latest code and restarts Flask.\nVLC keeps playing — safe mid-song.\nWeb UI will be unavailable for ~2 seconds.')) return;
    showSystemOverlay('Pulling latest code from GitHub...');
    const data = await apiCall('/system/update', {});
    if (data && data.success) {
        log(data.message, 'success');
        showSystemOverlay('Code updated. Restarting service...');
        await waitForRestart();
        location.reload();
    } else {
        hideSystemOverlay();
    }
}

// --- Karaoke Nerds Search ---

function extractYouTubeId(url) {
    const m = url.match(/(?:youtube\.com\/watch\?.*v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/);
    return m ? m[1] : null;
}

let knPreferredBrands = window.KJ_CONFIG.knPreferredBrands || [];
let knExpandedSongs = {};
let knSongData = {};

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
    knSongData = {};

    const downloadedIdToPath = new Map(
        localMediaItems.filter(i => i.youtube_id).map(i => [i.youtube_id, i.file_path])
    );

    songs.forEach((song, idx) => {
        const songId = `kn-song-${idx}`;
        const trackCount = song.tracks.length;
        const isExpanded = false;
        knExpandedSongs[songId] = isExpanded;
        knSongData[songId] = { song, catalogLoaded: false };

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
        header.appendChild(createCopyBtn(`${song.artist} - ${song.title}`));
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

            const videoId = extractYouTubeId(track.youtube_url);
            const downloadedPath = videoId ? downloadedIdToPath.get(videoId) : null;

            const actions = document.createElement('span');
            actions.className = 'kn-track-actions';

            if (downloadedPath) {
                const badge = document.createElement('span');
                badge.className = 'kn-downloaded-badge';
                badge.textContent = '\u2713 Downloaded';
                actions.appendChild(badge);

                const playBtn = document.createElement('button');
                playBtn.className = 'kn-play-btn';
                playBtn.textContent = 'Play';
                playBtn.onclick = (e) => {
                    e.stopPropagation();
                    playMedia(downloadedPath);
                };
                actions.appendChild(playBtn);
            } else {
                const dlBtn = document.createElement('button');
                dlBtn.className = 'kn-download-btn';
                dlBtn.textContent = 'Download';
                dlBtn.onclick = (e) => {
                    e.stopPropagation();
                    downloadKNTrack(track.youtube_url);
                };
                actions.appendChild(dlBtn);
            }

            trackEl.appendChild(info);
            trackEl.appendChild(actions);
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
    if (!isCollapsed) loadKNCatalogMatches(songId);
}

async function loadKNCatalogMatches(songId) {
    const data = knSongData[songId];
    if (!data || data.catalogLoaded) return;
    data.catalogLoaded = true;

    const { song } = data;
    const query = `${song.artist} ${song.title}`.trim();

    let results = [];
    try {
        const resp = await fetch(`/search?q=${encodeURIComponent(query)}&limit=5`);
        if (resp.ok) results = await resp.json();
    } catch (_) { /* catalog unavailable */ }

    if (!Array.isArray(results) || results.length === 0) return;

    const trackList = document.getElementById(songId);
    if (!trackList) return;

    const section = document.createElement('div');
    section.className = 'kn-local-section';

    const header = document.createElement('div');
    header.className = 'kn-local-header';
    header.textContent = `In your collection (${results.length})`;
    section.appendChild(header);

    results.forEach(match => {
        const row = document.createElement('div');
        row.className = 'kn-local-match';

        const detail = document.createElement('div');
        detail.className = 'catalog-detail';

        const titleRow = document.createElement('span');
        titleRow.textContent = match.filename.replace(/\.\w+$/, '') + ' ';
        if (match.format) {
            const badge = document.createElement('span');
            badge.className = `format-badge ${getFormatBadgeClass(match.format)}`;
            badge.textContent = match.format;
            titleRow.appendChild(badge);
        }
        detail.appendChild(titleRow);

        if (match.folder) {
            const folderSpan = document.createElement('div');
            folderSpan.className = 'catalog-folder';
            folderSpan.textContent = match.folder
                .replace(/^\/mnt\/[^/]+\//, '')
                .replace(/^\/Volumes\/[^/]+\//, '');
            folderSpan.title = match.folder;
            detail.appendChild(folderSpan);
        }

        const playBtn = document.createElement('button');
        playBtn.className = 'kn-play-btn';
        playBtn.textContent = 'Play';
        playBtn.onclick = (e) => {
            e.stopPropagation();
            playMedia(match.path);
        };

        row.appendChild(detail);
        row.appendChild(playBtn);
        section.appendChild(row);
    });

    trackList.insertBefore(section, trackList.firstChild);
}

function downloadKNTrack(youtubeUrl) {
    clearKNResults();
    log(`Queuing download: ${youtubeUrl}`);
    apiCall('/download', { url: youtubeUrl });
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
        row.appendChild(createCopyBtn(r.title));
        row.appendChild(dlBtn);
        container.appendChild(row);
    });
}

function downloadYTTrack(url) {
    clearYTResults();
    log(`Queuing download: ${url}`);
    apiCall('/download', { url });
}

function saveYTKaraokeToggle() {
    const checked = document.getElementById('yt-karaoke-prefix').checked;
    localStorage.setItem('ytKaraokePrefix', checked ? '1' : '0');
}

function loadYTKaraokeToggle() {
    const saved = localStorage.getItem('ytKaraokePrefix');
    document.getElementById('yt-karaoke-prefix').checked = saved === '1';
}

// --- Divebar Search ---

async function searchDivebar() {
    const input = document.getElementById('db-query');
    const btn = document.getElementById('db-search-btn');
    const status = document.getElementById('db-status');
    const query = input.value.trim();
    if (!query || query.length < 2) {
        log('Enter at least 2 characters to search.', 'error');
        return;
    }
    log(`Searching Divebar: ${query}`);
    btn.disabled = true;
    status.classList.remove('hidden');
    document.getElementById('db-stage').textContent = 'Searching Divebar catalog...';

    const data = await apiCall('/divebar/search', { query });

    btn.disabled = false;
    status.classList.add('hidden');
    if (data) {
        if (Array.isArray(data) && data.length === 0) {
            log('No results found in Divebar.', 'error');
            document.getElementById('db-results').innerHTML =
                '<div class="kn-no-results">No results found in Divebar catalog.</div>';
        } else if (data.error) {
            log(`Divebar search error: ${data.error}`, 'error');
        } else {
            const totalTracks = data.reduce((sum, s) => sum + (s.tracks ? s.tracks.length : 0), 0);
            log(`Found ${data.length} song${data.length !== 1 ? 's' : ''} (${totalTracks} tracks) in Divebar.`, 'success');
            renderDBResults(data);
        }
    }
}

function clearDBResults() {
    document.getElementById('db-results').innerHTML = '';
    document.getElementById('db-query').value = '';
}

function renderDBResults(songs) {
    const container = document.getElementById('db-results');
    container.innerHTML = '';

    songs.forEach((song, idx) => {
        const songId = `db-song-${idx}`;
        const trackCount = song.tracks ? song.tracks.length : 0;

        // Song header
        const header = document.createElement('div');
        header.className = 'kn-song-header db-song-header';
        header.onclick = () => {
            const el = document.getElementById(songId);
            if (el) {
                const collapsed = el.classList.toggle('collapsed');
                const chev = document.getElementById('db-chevron-' + idx);
                if (chev) chev.classList.toggle('expanded', !collapsed);
            }
        };

        const chevron = document.createElement('span');
        chevron.className = 'folder-chevron';
        chevron.id = 'db-chevron-' + idx;
        chevron.textContent = '\u25B6';

        const titleText = document.createElement('span');
        titleText.className = 'kn-song-title';
        titleText.textContent = `${song.title || 'Unknown'} \u2014 ${song.artist || 'Unknown'}`;

        const count = document.createElement('span');
        count.className = 'kn-track-count';
        count.textContent = `${trackCount} track${trackCount !== 1 ? 's' : ''}`;

        header.appendChild(chevron);
        header.appendChild(titleText);
        if (song.artist && song.title) {
            header.appendChild(createCopyBtn(`${song.artist} - ${song.title}`));
        }
        header.appendChild(count);
        container.appendChild(header);

        // Track list (collapsed by default)
        const trackList = document.createElement('div');
        trackList.className = 'kn-track-list collapsed';
        trackList.id = songId;

        if (song.tracks) {
            song.tracks.forEach(track => {
                const trackEl = document.createElement('div');
                trackEl.className = 'kn-track db-track';

                const info = document.createElement('span');
                info.className = 'kn-track-info';

                const brandSpan = document.createElement('span');
                brandSpan.className = 'kn-brand-name';
                brandSpan.textContent = track.brand || 'Unknown';
                info.appendChild(brandSpan);

                // Format + quality badge (e.g. "MP4 720p", "MP4 HD", "ZIP CDG")
                const fmtBadge = document.createElement('span');
                fmtBadge.className = 'format-badge db-format-badge';
                const fmt = (track.format || 'unknown').toUpperCase();
                const quality = track.quality || '';
                fmtBadge.textContent = quality ? `${fmt} ${quality}` : fmt;
                info.appendChild(fmtBadge);

                // File size
                if (track.file_size) {
                    const sizeSpan = document.createElement('span');
                    sizeSpan.className = 'db-file-size';
                    sizeSpan.textContent = formatFileSize(track.file_size);
                    info.appendChild(sizeSpan);
                }

                // Source badge (GCS = fast mirror, or Divebar = Google Drive)
                const badge = document.createElement('span');
                badge.className = 'kn-community-badge db-badge';
                if (track.in_gcs) {
                    badge.textContent = 'GCS';
                    badge.title = 'Download from GCS mirror (fast)';
                    badge.classList.add('db-gcs-badge');
                } else {
                    badge.textContent = 'Drive';
                    badge.title = 'Download from Google Drive';
                }
                info.appendChild(badge);

                const actions = document.createElement('span');
                actions.className = 'kn-track-actions';

                const dlBtn = document.createElement('button');
                dlBtn.className = 'kn-download-btn db-download-btn';
                dlBtn.textContent = 'Download';
                dlBtn.onclick = (e) => {
                    e.stopPropagation();
                    downloadDivebarTrack(track.file_id, track.drive_path || track.brand);
                    dlBtn.disabled = true;
                    dlBtn.textContent = 'Queued';
                };
                actions.appendChild(dlBtn);

                trackEl.appendChild(info);
                trackEl.appendChild(actions);
                trackList.appendChild(trackEl);
            });
        }

        container.appendChild(trackList);
    });
}

function downloadDivebarTrack(fileId, filename) {
    log(`Queuing Divebar download: ${filename}`);
    apiCall('/divebar/download', { file_id: fileId, filename: filename });
}

function formatFileSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// Divebar cross-reference for KN results
async function loadDivebarBadges() {
    // Placeholder for KN cross-reference badges (future enhancement)
}

// --- Divebar Status ---

let dbStatusCache = null;

async function fetchDbStatus() {
    try {
        const resp = await fetch('/divebar/status');
        if (!resp.ok) return null;
        return await resp.json();
    } catch (_) { return null; }
}

async function updateDbHealthDot() {
    const dot = document.getElementById('db-health-dot');
    if (!dot) return;
    const data = await fetchDbStatus();
    dbStatusCache = data;
    if (!data || !data.configured) {
        dot.className = 'yt-health-dot red';
        dot.title = 'Divebar not configured';
    } else if (data.gcs_mirror && data.gcs_mirror.percent >= 95) {
        dot.className = 'yt-health-dot green';
        dot.title = `GCS mirror ${data.gcs_mirror.percent}% synced`;
    } else if (data.gcs_mirror && data.gcs_mirror.percent > 0) {
        dot.className = 'yt-health-dot yellow';
        dot.title = `GCS mirror ${data.gcs_mirror.percent}% synced (${data.gcs_mirror.synced}/${data.catalog.total_files})`;
    } else {
        dot.className = 'yt-health-dot yellow';
        dot.title = 'GCS mirror sync starting...';
    }
}

function openDbStatusModal() {
    document.getElementById('db-modal').classList.remove('hidden');
    document.getElementById('db-modal-loading').classList.remove('hidden');
    document.getElementById('db-modal-body').classList.add('hidden');
    loadDbStatusModal();
}

function closeDbStatusModal() {
    document.getElementById('db-modal').classList.add('hidden');
}

async function loadDbStatusModal() {
    const data = dbStatusCache || await fetchDbStatus();
    dbStatusCache = data;

    const loading = document.getElementById('db-modal-loading');
    const body = document.getElementById('db-modal-body');
    loading.classList.add('hidden');
    body.classList.remove('hidden');

    if (!data || data.error) {
        body.innerHTML = `<div class="av-section"><p style="color:#f87171">${data?.error || 'Could not connect to Divebar API'}</p></div>`;
        return;
    }

    const c = data.catalog || {};
    const g = data.gcs_mirror || {};
    const f = data.formats || {};
    const x = data.cross_reference || {};
    const kn = data.karaoke_nerds || {};

    const lastSync = c.last_index_sync ? new Date(c.last_index_sync).toLocaleString() : 'Never';
    const lastXref = x.last_rebuild ? new Date(x.last_rebuild).toLocaleString() : 'Never';

    const mirrorColor = g.percent >= 95 ? '#22c55e' : g.percent > 0 ? '#eab308' : '#f87171';
    const mirrorLabel = g.percent >= 100 ? 'Fully synced' : `${g.percent}% synced`;

    // Format breakdown
    let fmtRows = '';
    for (const [fmt, info] of Object.entries(f)) {
        const pct = info.count > 0 ? Math.round(info.in_gcs / info.count * 100) : 0;
        fmtRows += `<tr><td>${fmt.toUpperCase()}</td><td>${info.count.toLocaleString()}</td><td>${info.gb} GB</td><td>${info.in_gcs.toLocaleString()} (${pct}%)</td></tr>`;
    }

    body.innerHTML = `
        <div class="av-section">
            <div class="av-section-title">Catalog Index</div>
            <div class="av-grid">
                <span class="av-label">Total files</span><span class="av-value">${c.total_files?.toLocaleString() || 0}</span>
                <span class="av-label">Brands</span><span class="av-value">${c.total_brands || 0}</span>
                <span class="av-label">With metadata</span><span class="av-value">${c.with_metadata?.toLocaleString() || 0}</span>
                <span class="av-label">Total size</span><span class="av-value">${c.total_gb || 0} GB</span>
                <span class="av-label">Last index sync</span><span class="av-value">${lastSync}</span>
            </div>
        </div>
        <div class="av-section">
            <div class="av-section-title">GCS Mirror</div>
            <div class="db-progress-bar">
                <div class="db-progress-fill" style="width:${Math.min(g.percent || 0, 100)}%; background:${mirrorColor}"></div>
            </div>
            <div class="av-grid">
                <span class="av-label">Status</span><span class="av-value" style="color:${mirrorColor}">${mirrorLabel}</span>
                <span class="av-label">Files synced</span><span class="av-value">${g.synced?.toLocaleString() || 0} / ${c.total_files?.toLocaleString() || 0}</span>
                <span class="av-label">Data synced</span><span class="av-value">${g.synced_gb || 0} / ${c.total_gb || 0} GB</span>
                <span class="av-label">Pending</span><span class="av-value">${g.pending?.toLocaleString() || 0} files (${g.pending_gb || 0} GB)</span>
            </div>
        </div>
        <div class="av-section">
            <div class="av-section-title">Formats</div>
            <table class="db-format-table">
                <thead><tr><th>Format</th><th>Files</th><th>Size</th><th>In GCS</th></tr></thead>
                <tbody>${fmtRows}</tbody>
            </table>
        </div>
        <div class="av-section">
            <div class="av-section-title">KaraokeNerds Data</div>
            <div class="av-grid">
                <span class="av-label">Song catalog</span><span class="av-value">${kn.songs?.toLocaleString() || 0} songs</span>
                <span class="av-label">Community tracks</span><span class="av-value">${kn.community_tracks?.toLocaleString() || 0} tracks</span>
                <span class="av-label">KN cross-references</span><span class="av-value">${x.total_matches?.toLocaleString() || 0} matches</span>
                <span class="av-label">Last xref rebuild</span><span class="av-value">${lastXref}</span>
            </div>
        </div>
    `;
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
        // Escape closes modals
        if (e.key === 'Escape') {
            if (!document.getElementById('overlay-modal').classList.contains('hidden')) {
                hideOverlayForm();
            }
            if (!document.getElementById('av-modal').classList.contains('hidden')) {
                closeAvModal();
            }
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

    // Restore VNC size preference and hidden state
    if (localStorage.getItem('kj-vnc-hidden') === '1') {
        hideVncPreview();
    } else {
        const savedVncSize = localStorage.getItem('kj-vnc-size');
        if (savedVncSize) setVncSize(savedVncSize);
    }

    updateMediaFilterBtn();

    loadYTKaraokeToggle();
    updateDbHealthDot();
    updateStatus();
    updateMediaList();
    updateFillerMusicList();
    loadOverlays();
    checkCatalogAvailability();
    fetchRotation();
    fetchAutoDeployStatus();
    fetchSleepModeStatus();
    log('Nomad KJ Control initialized.');
});

setInterval(updateStatus, 2000);
setInterval(fetchRotation, 10000);

// --- System Stats ---

const SPARK_MAX = 30;
const cpuHistory = [];
const memHistory = [];

async function fetchSystemStats() {
    try {
        const resp = await fetch('/system/stats');
        if (!resp.ok) return;
        const d = await resp.json();

        // Update bars
        const cpuBar = document.getElementById('stat-cpu-bar');
        const memBar = document.getElementById('stat-mem-bar');
        const diskBar = document.getElementById('stat-disk-bar');
        if (cpuBar) cpuBar.style.width = d.cpu_percent + '%';
        if (memBar) memBar.style.width = d.mem_percent + '%';
        if (diskBar) diskBar.style.width = d.disk_percent + '%';

        // Warn/crit colors
        [cpuBar, memBar, diskBar].forEach((bar, i) => {
            if (!bar) return;
            const val = [d.cpu_percent, d.mem_percent, d.disk_percent][i];
            bar.classList.toggle('stat-warn', val >= 75 && val < 90);
            bar.classList.toggle('stat-crit', val >= 90);
        });

        // Update values
        const cpuVal = document.getElementById('stat-cpu-val');
        const memVal = document.getElementById('stat-mem-val');
        const diskVal = document.getElementById('stat-disk-val');
        if (cpuVal) cpuVal.textContent = Math.round(d.cpu_percent) + '%';
        if (memVal) memVal.textContent = d.mem_used_gb + '/' + d.mem_total_gb + ' GB';
        if (diskVal) diskVal.textContent = d.disk_used_gb + '/' + d.disk_total_gb + ' GB';

        // Sparklines (CPU + MEM only)
        cpuHistory.push(d.cpu_percent);
        memHistory.push(d.mem_percent);
        if (cpuHistory.length > SPARK_MAX) cpuHistory.shift();
        if (memHistory.length > SPARK_MAX) memHistory.shift();
        renderSparkline('stat-cpu-spark', cpuHistory, '');
        renderSparkline('stat-mem-spark', memHistory, 'spark-mem');
    } catch (_) {}
}

function renderSparkline(id, data, cls) {
    const el = document.getElementById(id);
    if (!el) return;
    // Reuse existing bars or create new ones
    while (el.children.length > data.length) el.removeChild(el.lastChild);
    while (el.children.length < data.length) {
        const bar = document.createElement('div');
        bar.className = 'sys-stat-spark-bar' + (cls ? ' ' + cls : '');
        el.appendChild(bar);
    }
    const unit = cls === 'spark-mem' ? ' GB' : '%';
    data.forEach((val, i) => {
        const bar = el.children[i];
        bar.style.height = Math.max(1, val / 100 * 20) + 'px';
        const ago = (data.length - 1 - i) * 5;
        const timeLabel = ago === 0 ? 'now' : ago + 's ago';
        bar.title = Math.round(val) + unit + ' (' + timeLabel + ')';
    });
}

fetchSystemStats();
setInterval(fetchSystemStats, 5000);

// --- Rotation (SQLite primary) ---

let rotationData = [];

async function fetchRotation() {
    try {
        const response = await fetch('/rotation');
        if (!response.ok) {
            if (response.status === 503) {
                const panel = document.querySelector('.rotation-panel');
                if (panel) panel.style.display = 'none';
                return;
            }
            throw new Error('Failed to fetch rotation');
        }
        const data = await response.json();
        rotationData = data.entries || [];
        renderRotation(rotationData);
    } catch (e) {
        const list = document.getElementById('rotation-list');
        if (list) list.innerHTML = '<div class="rotation-empty">Could not load rotation</div>';
    }
}

function renderRotation(entries) {
    const list = document.getElementById('rotation-list');
    if (!list) return;

    // Don't re-render while a row is being edited — would destroy the edit inputs
    if (document.querySelector('.rotation-editing')) return;

    if (!entries.length) {
        list.innerHTML = '<div class="rotation-empty">No singers in queue</div>';
        return;
    }

    list.innerHTML = '';
    entries.forEach((entry, idx) => {
        const row = document.createElement('div');
        row.className = 'rotation-entry';
        const statusLower = (entry.status || '').toLowerCase();
        if (statusLower.includes('singing') || statusLower === 'now singing') {
            row.classList.add('rotation-singing');
        } else if (statusLower.includes('next')) {
            row.classList.add('rotation-next');
        } else if (statusLower.includes('on hold') || statusLower.includes('brb')) {
            row.classList.add('rotation-onhold');
        } else if (statusLower === 'skipped') {
            row.classList.add('rotation-skipped');
        }

        // Modifier+hover: show edit/delete indicator
        row.addEventListener('mouseenter', (e) => {
            if (e.ctrlKey || e.metaKey) row.classList.add('rotation-delete-hover');
            else if (e.shiftKey) row.classList.add('rotation-edit-hover');
        });
        row.addEventListener('mouseleave', () => {
            row.classList.remove('rotation-delete-hover', 'rotation-edit-hover');
        });

        // Row click: Shift=edit, Ctrl/Cmd=delete
        row.addEventListener('click', (e) => {
            // Don't intercept clicks on buttons/inputs or during editing
            if (e.target.closest('button, input') || row.classList.contains('rotation-editing')) return;
            if (e.shiftKey) {
                e.preventDefault();
                enterRotationEditMode(row, entry);
            } else if (e.ctrlKey || e.metaKey) {
                e.preventDefault();
                deleteRotationEntry(entry.id, entry.singer);
            }
        });

        // Drag handle
        const handle = document.createElement('span');
        handle.className = 'rotation-drag-handle';
        handle.textContent = '\u2261';  // ≡ hamburger icon
        handle.draggable = true;
        handle.addEventListener('dragstart', (e) => {
            row.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', String(idx));
            e.dataTransfer.setDragImage(row, 0, 0);
        });
        handle.addEventListener('dragend', () => {
            row.classList.remove('dragging');
            list.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
        });

        row.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            list.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
            row.classList.add('drag-over');
        });
        row.addEventListener('dragleave', () => {
            row.classList.remove('drag-over');
        });
        row.addEventListener('drop', (e) => {
            e.preventDefault();
            row.classList.remove('drag-over');
            const fromIdx = parseInt(e.dataTransfer.getData('text/plain'), 10);
            const toIdx = idx;
            if (fromIdx !== toIdx) {
                moveRotationEntry(entries[fromIdx].id, entries[toIdx].position);
            }
        });

        const info = document.createElement('div');
        info.className = 'rotation-info';

        const num = document.createElement('span');
        num.className = 'rotation-num';
        num.textContent = (idx + 1) + '.';

        const name = document.createElement('span');
        name.className = 'rotation-name rotation-copyable';
        name.textContent = entry.singer;
        name.title = 'Click to copy \u2022 Shift+click to edit';
        name.onclick = (e) => { if (!e.shiftKey && !e.ctrlKey && !e.metaKey) copyRotationText(name); };

        const song = document.createElement('span');
        song.className = 'rotation-song rotation-copyable';
        song.textContent = entry.song_artist || '';
        song.title = 'Click to copy \u2022 Shift+click to edit';
        song.onclick = (e) => { if (!e.shiftKey && !e.ctrlKey && !e.metaKey) copyRotationText(song); };

        row.appendChild(handle);
        info.appendChild(num);
        info.appendChild(name);
        if (entry.song_artist) info.appendChild(song);

        if (entry.duration) {
            const dur = document.createElement('span');
            dur.className = 'rotation-duration';
            const mins = Math.floor(entry.duration / 60);
            const secs = entry.duration % 60;
            dur.textContent = mins + ':' + String(secs).padStart(2, '0');
            info.appendChild(dur);
        }

        if (entry.estimated_time) {
            const est = document.createElement('span');
            est.className = 'rotation-estimate';
            est.textContent = '~' + entry.estimated_time;
            est.title = 'Estimated sing time';
            info.appendChild(est);
        }

        const badge = document.createElement('span');
        badge.className = 'rotation-badge';
        if (statusLower.includes('singing') || statusLower === 'now singing') {
            badge.textContent = 'NOW';
            badge.classList.add('badge-now');
        } else if (statusLower.includes('next')) {
            badge.textContent = 'NEXT';
            badge.classList.add('badge-next');
        } else if (statusLower === 'waiting') {
            badge.textContent = 'WAITING';
            badge.classList.add('badge-waiting');
        } else if (statusLower.includes('being made')) {
            badge.textContent = 'MAKING';
            badge.classList.add('badge-wip');
        } else if (statusLower.includes('on hold') || statusLower.includes('brb')) {
            badge.textContent = 'BRB';
            badge.classList.add('badge-onhold');
        } else if (statusLower === 'skipped') {
            badge.textContent = 'SKIP';
            badge.classList.add('badge-skipped');
        }
        if (badge.textContent) info.appendChild(badge);

        // Preparation status badge
        const prepBadge = document.createElement('span');
        prepBadge.className = 'rotation-prep-badge';
        if (entry.file_path) {
            prepBadge.textContent = 'READY';
            prepBadge.classList.add('prep-ready');
        } else if (entry.download_status === 'queued' || entry.download_status === 'downloading') {
            prepBadge.textContent = 'DOWNLOADING';
            prepBadge.classList.add(entry.download_source === 'youtube' ? 'prep-downloading-orange' : 'prep-downloading-green');
        } else if (entry.download_status === 'failed') {
            prepBadge.textContent = 'FAILED';
            prepBadge.classList.add('prep-failed');
        } else if (entry.url_fallback) {
            prepBadge.textContent = 'URL';
            prepBadge.classList.add('prep-url');
        } else if (entry.gen_status === 'processing') {
            prepBadge.textContent = 'MAKING';
            prepBadge.classList.add('prep-making');
        } else if (entry.gen_status === 'awaiting_review') {
            prepBadge.textContent = 'NEEDS REVIEW';
            prepBadge.classList.add('prep-review');
            prepBadge.style.cursor = 'pointer';
            prepBadge.title = 'Click to review lyrics';
            prepBadge.onclick = (e) => {
                e.stopPropagation();
                window.open('https://gen.nomadkaraoke.com/app/jobs#/' + entry.gen_job_id + '/review', '_blank');
            };
        } else if (entry.gen_status === 'rendering') {
            prepBadge.textContent = 'RENDERING';
            prepBadge.classList.add('prep-rendering');
        } else {
            prepBadge.textContent = 'UNLINKED';
            prepBadge.classList.add('prep-unlinked');
        }
        info.appendChild(prepBadge);

        const actions = document.createElement('div');
        actions.className = 'rotation-actions';

        if (entry.file_path) {
            const playBtn = document.createElement('button');
            playBtn.className = 'rotation-btn rotation-btn-play';
            playBtn.textContent = '\u25B6';  // ▶
            playBtn.title = 'Play this song';
            playBtn.onclick = () => playAndAdvanceRotation(entry, idx, entries);
            actions.appendChild(playBtn);
        } else if (entry.url_fallback) {
            const playBtn = document.createElement('button');
            playBtn.className = 'rotation-btn rotation-btn-play';
            playBtn.textContent = '\u25B6';  // ▶
            playBtn.title = 'Play via browser mode';
            playBtn.onclick = () => { enableBrowserMode(entry.url_fallback); advanceRotationStatus(entry, idx, entries); };
            actions.appendChild(playBtn);
        } else if (!entry.download_status || entry.download_status === 'failed') {
            const linkBtn = document.createElement('button');
            linkBtn.className = 'rotation-btn rotation-btn-link';
            linkBtn.textContent = '\uD83D\uDD17';  // 🔗
            linkBtn.title = 'Search and link a song';
            linkBtn.onclick = () => openLinkSearch(entry.id, entry.song_artist);
            actions.appendChild(linkBtn);
        }

        if (!statusLower.includes('singing') && statusLower !== 'now singing') {
            const singBtn = document.createElement('button');
            singBtn.className = 'rotation-btn rotation-btn-sing';
            singBtn.textContent = 'Singing';
            singBtn.onclick = () => updateRotationStatus(entry.id, 'Now Singing');
            actions.appendChild(singBtn);
        }

        const doneBtn = document.createElement('button');
        doneBtn.className = 'rotation-btn rotation-btn-done';
        doneBtn.textContent = 'Done';
        doneBtn.onclick = () => updateRotationStatus(entry.id, 'Done');
        actions.appendChild(doneBtn);

        if (!statusLower.includes('next')) {
            const nextBtn = document.createElement('button');
            nextBtn.className = 'rotation-btn rotation-btn-next';
            nextBtn.textContent = 'Next';
            nextBtn.onclick = () => updateRotationStatus(entry.id, 'Up Next');
            actions.appendChild(nextBtn);
        }

        // "..." more status options
        const moreBtn = document.createElement('button');
        moreBtn.className = 'rotation-btn rotation-btn-more';
        moreBtn.textContent = '\u2026';
        moreBtn.title = 'More status options';
        moreBtn.onclick = (e) => {
            e.stopPropagation();
            document.querySelectorAll('.rotation-dropdown').forEach(d => d.remove());
            const dropdown = document.createElement('div');
            dropdown.className = 'rotation-dropdown';
            const allStatuses = ['Now Singing', 'Up Next', 'Waiting', 'Done', 'Being Made (!)', 'On Hold (BRB)', 'Skipped'];
            allStatuses.forEach(s => {
                const item = document.createElement('button');
                item.className = 'rotation-dropdown-item';
                item.textContent = s;
                item.onclick = (ev) => {
                    ev.stopPropagation();
                    dropdown.remove();
                    updateRotationStatus(entry.id, s);
                };
                dropdown.appendChild(item);
            });
            // Add Unlink option if entry has a linked file
            if (entry.file_path || entry.url_fallback) {
                const sep = document.createElement('div');
                sep.className = 'rotation-dropdown-sep';
                dropdown.appendChild(sep);
                const unlinkItem = document.createElement('button');
                unlinkItem.className = 'rotation-dropdown-item rotation-dropdown-item-danger';
                unlinkItem.textContent = 'Unlink Song';
                unlinkItem.onclick = async (ev) => {
                    ev.stopPropagation();
                    dropdown.remove();
                    try {
                        const resp = await fetch('/rotation/unlink', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ id: entry.id }),
                        });
                        const data = await resp.json();
                        if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
                        showRotationIndicator('success');
                    } catch (err) {
                        showRotationIndicator('error');
                    }
                };
                dropdown.appendChild(unlinkItem);
            }
            row.appendChild(dropdown);
            const close = () => { dropdown.remove(); document.removeEventListener('click', close); };
            setTimeout(() => document.addEventListener('click', close), 0);
        };
        actions.appendChild(moreBtn);

        // Edit pencil button
        const editBtn = document.createElement('button');
        editBtn.className = 'rotation-btn rotation-btn-edit';
        editBtn.innerHTML = '&#9998;';
        editBtn.title = 'Edit singer/song';
        editBtn.onclick = (e) => {
            e.stopPropagation();
            enterRotationEditMode(row, entry);
        };
        actions.appendChild(editBtn);

        row.appendChild(info);
        row.appendChild(actions);
        list.appendChild(row);
    });
}

function enterRotationEditMode(row, entry) {
    // Don't double-enter edit mode
    if (row.classList.contains('rotation-editing')) return;
    row.classList.add('rotation-editing');

    const info = row.querySelector('.rotation-info');
    const actions = row.querySelector('.rotation-actions');

    // Save original content for cancel
    const origInfoHTML = info.innerHTML;
    const origActionsHTML = actions.innerHTML;

    // Replace info with editable inputs
    info.innerHTML = '';
    const singerInput = document.createElement('input');
    singerInput.type = 'text';
    singerInput.className = 'rotation-edit-input rotation-edit-singer';
    singerInput.value = entry.singer;
    singerInput.placeholder = 'Singer name';

    const songInput = document.createElement('input');
    songInput.type = 'text';
    songInput.className = 'rotation-edit-input rotation-edit-song';
    songInput.value = entry.song_artist || '';
    songInput.placeholder = 'Song & Artist';

    info.appendChild(singerInput);
    info.appendChild(songInput);

    // Replace actions with save/cancel/delete
    actions.innerHTML = '';
    const saveBtn = document.createElement('button');
    saveBtn.className = 'rotation-btn rotation-btn-save';
    saveBtn.textContent = 'Save';
    saveBtn.onclick = (e) => {
        e.stopPropagation();
        const newSinger = singerInput.value.trim();
        const newSong = songInput.value.trim();
        if (!newSinger) { singerInput.focus(); return; }
        saveRotationEdit(entry.id, newSinger, newSong);
    };

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'rotation-btn rotation-btn-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = (e) => {
        e.stopPropagation();
        info.innerHTML = origInfoHTML;
        actions.innerHTML = origActionsHTML;
        row.classList.remove('rotation-editing');
        // Re-render to restore event listeners
        renderRotation(rotationData);
    };

    const delBtn = document.createElement('button');
    delBtn.className = 'rotation-btn rotation-btn-delete';
    delBtn.textContent = 'Delete';
    delBtn.onclick = (e) => {
        e.stopPropagation();
        deleteRotationEntry(entry.id, entry.singer);
    };

    actions.appendChild(saveBtn);
    actions.appendChild(cancelBtn);
    actions.appendChild(delBtn);

    singerInput.focus();
    singerInput.select();

    // Stop all events from bubbling out of edit inputs
    [singerInput, songInput].forEach(input => {
        input.addEventListener('click', (e) => e.stopPropagation());
        input.addEventListener('keydown', (e) => e.stopPropagation());
        input.addEventListener('keyup', (e) => e.stopPropagation());
    });

    // Enter on singer: move to song input. Enter on song: save. Escape: cancel.
    singerInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); songInput.focus(); songInput.select(); }
        else if (e.key === 'Escape') { cancelBtn.click(); }
    });
    songInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { saveBtn.click(); }
        else if (e.key === 'Escape') { cancelBtn.click(); }
    });
}

async function saveRotationEdit(entryId, singer, songArtist) {
    showRotationIndicator('spin');
    try {
        const response = await fetch('/rotation/edit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: entryId, singer, song_artist: songArtist })
        });
        const data = await response.json();
        if (!response.ok) {
            showRotationIndicator('error');
            return;
        }
        if (data.entries) {
            rotationData = data.entries;
            // Clear editing state so renderRotation doesn't skip the re-render
            const editingRow = document.querySelector('.rotation-editing');
            if (editingRow) editingRow.classList.remove('rotation-editing');
            renderRotation(rotationData);
        }
        showRotationIndicator('success');
    } catch (e) {
        showRotationIndicator('error');
    }
}

async function deleteRotationEntry(entryId, singerName) {
    if (!confirm(`Delete "${singerName}" from rotation?`)) return;
    showRotationIndicator('spin');
    try {
        const response = await fetch('/rotation/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: entryId })
        });
        const data = await response.json();
        if (!response.ok) {
            showRotationIndicator('error');
            return;
        }
        if (data.entries) {
            rotationData = data.entries;
            // Clear editing state so renderRotation doesn't skip the re-render
            const editingRow = document.querySelector('.rotation-editing');
            if (editingRow) editingRow.classList.remove('rotation-editing');
            renderRotation(rotationData);
        }
        showRotationIndicator('success');
    } catch (e) {
        showRotationIndicator('error');
    }
}

function copyRotationText(el) {
    navigator.clipboard.writeText(el.textContent).then(() => {
        el.classList.add('rotation-copied');
        setTimeout(() => el.classList.remove('rotation-copied'), 600);
    });
}

function showRotationIndicator(state) {
    const el = document.getElementById('rotation-indicator');
    el.className = 'rotation-indicator';
    if (state === 'spin') {
        el.classList.add('spinning');
    } else if (state === 'success') {
        el.classList.add('success');
        setTimeout(() => el.classList.add('hidden'), 1500);
    } else if (state === 'error') {
        el.classList.add('error');
        setTimeout(() => el.classList.add('hidden'), 3000);
    } else {
        el.classList.add('hidden');
    }
}

async function playAndAdvanceRotation(entry, idx, entries) {
    playMedia(entry.file_path);
    advanceRotationStatus(entry, idx, entries);
}

async function advanceRotationStatus(entry, idx, entries) {
    // Mark this entry as singing
    await updateRotationStatus(entry.id, 'Now Singing');
    // Mark the next entry as up next (if there is one)
    const nextEntry = entries[idx + 1];
    if (nextEntry) {
        await updateRotationStatus(nextEntry.id, 'Up Next');
    }
}

async function updateRotationStatus(entryId, status) {
    showRotationIndicator('spin');
    try {
        const response = await fetch('/rotation/status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: entryId, status: status })
        });
        const data = await response.json();
        if (!response.ok) {
            showRotationIndicator('error');
            return;
        }
        if (data.entries) {
            rotationData = data.entries;
            renderRotation(rotationData);
        }
        showRotationIndicator('success');
    } catch (e) {
        showRotationIndicator('error');
    }
}

async function moveRotationEntry(entryId, newPosition) {
    showRotationIndicator('spin');
    try {
        const response = await fetch('/rotation/move', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: entryId, new_position: newPosition })
        });
        const data = await response.json();
        if (!response.ok) {
            showRotationIndicator('error');
            return;
        }
        if (data.entries) {
            rotationData = data.entries;
            renderRotation(rotationData);
        }
        showRotationIndicator('success');
    } catch (e) {
        showRotationIndicator('error');
    }
}

async function fetchSyncStatus() {
    try {
        const resp = await fetch('/rotation/sync-status');
        if (!resp.ok) return;
        const data = await resp.json();
        const dot = document.getElementById('rotation-sync-dot');
        if (!dot) return;
        dot.className = 'rotation-sync-dot';
        if (data.is_online) {
            dot.classList.add('sync-online');
            dot.title = 'Synced: ' + (data.last_sync || 'unknown');
        } else if (data.next_sync_in) {
            dot.classList.add('sync-offline');
            dot.title = 'Offline — sync will resume when connected';
        } else {
            dot.classList.add('sync-disabled');
            dot.title = 'Sheet sync not configured';
        }
    } catch (e) { /* ignore */ }
}
setInterval(fetchSyncStatus, 30000);

async function restoreFromSheet() {
    if (!confirm('Restore rotation from Google Sheet backup?\n\nThis will replace the current rotation with the last synced state.')) return;
    showRotationIndicator('spin');
    try {
        const resp = await fetch('/rotation/restore', { method: 'POST', headers: {'Content-Type': 'application/json'} });
        const data = await resp.json();
        if (!resp.ok) { showRotationIndicator('error'); alert('Restore failed: ' + (data.error || 'Unknown')); return; }
        if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
        showRotationIndicator('success');
    } catch (e) { showRotationIndicator('error'); }
}

function openLinkSearch(entryId, songText) {
    const form = document.getElementById('rotation-add-form');
    if (form.classList.contains('hidden')) form.classList.remove('hidden');
    // Store the target entry ID so selectRotSearchResult can link instead of add
    form.dataset.linkTargetId = entryId;
    form.classList.add('link-mode');
    // Find the entry from rotationData for the banner
    const entry = rotationData.find(e => e.id === entryId);
    const singerName = entry ? entry.singer : '#' + entryId;
    const entrySong = entry ? entry.song_artist : '';
    const bannerText = entrySong ? singerName + ' — ' + entrySong : singerName;
    document.getElementById('rotation-link-singer-name').textContent = bannerText;
    document.getElementById('rotation-link-banner').classList.remove('hidden');
    const songInput = document.getElementById('rotation-song');
    songInput.placeholder = 'Search for song to link...';
    songInput.value = songText || '';
    songInput.select();
    songInput.focus();
    if (songText && songText.length >= 3) {
        songInput.dispatchEvent(new Event('input'));
    }
}

function exitLinkMode() {
    const form = document.getElementById('rotation-add-form');
    delete form.dataset.linkTargetId;
    form.classList.remove('link-mode');
    document.getElementById('rotation-link-banner').classList.add('hidden');
    document.getElementById('rotation-song').placeholder = 'Song & Artist';
    document.getElementById('rotation-song').value = '';
    hideRotSearchDropdown();
    form.classList.add('hidden');
}

function toggleRotationAddForm() {
    const form = document.getElementById('rotation-add-form');
    // If in link mode, exit link mode instead of toggling
    if (form.dataset.linkTargetId) {
        exitLinkMode();
        return;
    }
    form.classList.toggle('hidden');
    if (!form.classList.contains('hidden')) {
        document.getElementById('rotation-singer').focus();
    }
}

async function addRotationEntry() {
    const singerInput = document.getElementById('rotation-singer');
    const songInput = document.getElementById('rotation-song');
    const singer = singerInput.value.trim();
    const songArtist = songInput.value.trim();
    if (!singer) return;

    showRotationIndicator('spin');
    try {
        const response = await fetch('/rotation/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ singer, song_artist: songArtist })
        });
        const data = await response.json();
        if (!response.ok) {
            showRotationIndicator('error');
            return;
        }
        if (data.entries) {
            rotationData = data.entries;
            renderRotation(rotationData);
        }
        singerInput.value = '';
        songInput.value = '';
        singerInput.focus();
        showRotationIndicator('success');
    } catch (e) {
        hideRotSearchDropdown();
        showRotationIndicator('error');
    }
}

// --- Rotation Search-As-You-Type ---

let rotSearchTimer = null;
let rotSearchSelectedIdx = -1;
let rotSearchResults = [];

function initRotationSearch() {
    const songInput = document.getElementById('rotation-song');
    if (!songInput) return;

    songInput.addEventListener('input', () => {
        clearTimeout(rotSearchTimer);
        const query = songInput.value.trim();
        if (query.length < 3) {
            hideRotSearchDropdown();
            return;
        }
        rotSearchTimer = setTimeout(() => doRotationSearch(query), 300);
    });

    songInput.addEventListener('keydown', (e) => {
        const dropdown = document.getElementById('rotation-search-dropdown');
        const form = document.getElementById('rotation-add-form');
        if (!dropdown || dropdown.classList.contains('hidden')) {
            if (e.key === 'Enter') addRotationEntry();
            if (e.key === 'Escape' && form && form.dataset.linkTargetId) { exitLinkMode(); return; }
            return;
        }

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            rotSearchSelectedIdx = Math.min(rotSearchSelectedIdx + 1, rotSearchResults.length - 1);
            highlightRotSearchResult();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            rotSearchSelectedIdx = Math.max(rotSearchSelectedIdx - 1, -1);
            highlightRotSearchResult();
        } else if (e.key === 'Enter' && rotSearchSelectedIdx >= 0) {
            e.preventDefault();
            selectRotSearchResult(rotSearchResults[rotSearchSelectedIdx]);
        } else if (e.key === 'Enter') {
            // Enter with no selection = add without linking
            hideRotSearchDropdown();
            addRotationEntry();
        } else if (e.key === 'Escape') {
            hideRotSearchDropdown();
            if (form && form.dataset.linkTargetId) exitLinkMode();
        } else if (e.key === 'Tab') {
            hideRotSearchDropdown();
        }
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.rotation-add-form, .rotation-search-dropdown')) {
            hideRotSearchDropdown();
        }
    });
}

async function doRotationSearch(query) {
    try {
        const resp = await fetch('/rotation/search?q=' + encodeURIComponent(query));
        if (!resp.ok) return;
        const data = await resp.json();
        renderRotSearchDropdown(data);
    } catch (e) {
        hideRotSearchDropdown();
    }
}

function renderRotSearchDropdown(data) {
    const dropdown = document.getElementById('rotation-search-dropdown');
    if (!dropdown) return;
    rotSearchResults = [];
    rotSearchSelectedIdx = -1;

    const localResults = data.local || [];
    const knSongs = data.karaoke_nerds || [];
    const prefUpper = knPreferredBrands.map(b => b.toUpperCase());
    const downloadedIdToPath = new Map(
        localMediaItems.filter(i => i.youtube_id).map(i => [i.youtube_id, i.file_path])
    );

    let html = '';

    if (localResults.length === 0 && knSongs.length === 0) {
        html = '<div class="search-header">No results found</div>';
        html += '<div class="rotation-search-hint">\u2191\u2193 navigate \u00B7 Enter select \u00B7 Tab skip \u00B7 Esc close</div>';
        dropdown.innerHTML = html;
        dropdown.classList.remove('hidden');
        return;
    }

    // --- "IN YOUR COLLECTION" section (local matches) ---
    if (localResults.length > 0) {
        html += '<div class="kn-local-section">';
        html += '<div class="kn-local-header">In your collection (' + localResults.length + ')</div>';
        localResults.forEach(match => {
            const idx = rotSearchResults.length;
            rotSearchResults.push({
                type: 'local', path: match.path, duration: match.duration,
                song_artist: (match.title || '') + ' - ' + (match.artist || ''),
            });
            const fname = match.filename ? match.filename.replace(/\.\w+$/, '') : (match.disc_id || '') + ' - ' + (match.artist || '') + ' - ' + (match.title || '');
            const formatClass = match.format ? getFormatBadgeClass(match.format) : 'other';
            html += '<div class="kn-local-match rs-clickable' + (idx === rotSearchSelectedIdx ? ' selected' : '') + '" data-idx="' + idx + '" onclick="selectRotSearchResult(rotSearchResults[' + idx + '])">';
            html += '<div class="catalog-detail">';
            html += '<span>' + escHtml(fname) + ' ';
            if (match.format) html += '<span class="format-badge ' + formatClass + '">' + escHtml(match.format) + '</span>';
            html += '</span>';
            if (match.path) {
                const folder = match.path.replace(/\/[^/]+$/, '').replace(/^\/media\/nomad\//, '').replace(/^\/opt\/nomad\//, '');
                html += '<div class="catalog-folder" title="' + escHtml(match.path) + '">' + escHtml(folder) + '</div>';
            }
            html += '</div>';
            html += '<span class="kn-play-btn">Link</span>';
            html += '</div>';
        });
        html += '</div>';
    }

    // --- KN tracks (same rendering as KN search) ---
    knSongs.forEach(song => {
        const sorted = sortKNTracks(song.tracks || []);
        sorted.forEach(track => {
            // Don't skip in_library tracks — show all versions like KN panel does
            const isCommunity = !!track.is_community;
            const isPreferred = prefUpper.includes((track.brand_code || '').toUpperCase());
            const videoId = extractYouTubeId(track.youtube_url || '');
            const downloadedPath = videoId ? downloadedIdToPath.get(videoId) : null;
            const idx = rotSearchResults.length;

            // Build result object for selectRotSearchResult
            const result = { song_artist: song.title + ' - ' + song.artist };
            if (downloadedPath) {
                result.type = 'local';
                result.path = downloadedPath;
            } else if (track.divebar) {
                result.type = 'divebar';
                result.file_id = track.divebar.file_id;
                result.filename = (track.brand_code || 'DB') + ' - ' + song.artist + ' - ' + song.title + '.mp4';
            } else if (track.youtube_url) {
                result.type = 'youtube';
                result.youtube_url = track.youtube_url;
                result.filename = (track.brand_code || 'YT') + ' - ' + song.artist + ' - ' + song.title + '.mp4';
            } else {
                return;
            }
            rotSearchResults.push(result);

            const rowClass = isCommunity ? ' community' : isPreferred ? ' preferred' : '';
            html += '<div class="kn-track' + rowClass + (idx === rotSearchSelectedIdx ? ' selected' : '') + ' rs-clickable" data-idx="' + idx + '" onclick="selectRotSearchResult(rotSearchResults[' + idx + '])">';
            html += '<span class="kn-track-info">';
            html += '<span class="kn-brand-name">' + escHtml(track.brand_name || '') + '</span>';
            html += '<span class="kn-brand-code">' + escHtml(track.brand_code || '') + '</span>';
            if (isCommunity) html += '<span class="kn-community-badge">Community</span>';
            else if (isPreferred) html += '<span class="kn-preferred-badge">\u2605</span>';
            html += '<span class="kn-song-title">' + escHtml(song.title + ' - ' + song.artist) + '</span>';
            html += '</span>';
            html += '<span class="kn-track-actions">';
            if (downloadedPath) {
                html += '<span class="kn-downloaded-badge">\u2713 Downloaded</span>';
                html += '<span class="kn-play-btn">Link</span>';
            } else {
                html += '<span class="kn-download-btn">DL & Link</span>';
            }
            html += '</span>';
            html += '</div>';
        });
    });

    // MAKE option always at the bottom
    const songInput = document.getElementById('rotation-song');
    const rawQuery = songInput ? songInput.value.trim() : '';
    const makeIdx = rotSearchResults.length;
    rotSearchResults.push({
        type: 'make', badge: 'MAKE', badgeClass: 'search-badge-make',
        title: 'Create karaoke video for: ' + rawQuery,
        meta: 'Generate via Nomad Gen \u00B7 Takes ~5 min',
        rawQuery: rawQuery,
    });
    html += '<div class="rotation-search-result' + (makeIdx === rotSearchSelectedIdx ? ' selected' : '') + '" data-idx="' + makeIdx + '" onclick="selectRotSearchResult(rotSearchResults[' + makeIdx + '])">' +
        '<span class="search-badge search-badge-make">MAKE</span>' +
        '<div class="search-info">' +
            '<div class="search-title">' + escHtml('Create karaoke video for: ' + rawQuery) + '</div>' +
            '<div class="search-meta">Generate via Nomad Gen \u00B7 Takes ~5 min</div>' +
        '</div>' +
    '</div>';

    html += '<div class="rotation-search-hint">\u2191\u2193 navigate \u00B7 Enter select \u00B7 Tab skip \u00B7 Esc close</div>';

    dropdown.innerHTML = html;
    dropdown.classList.remove('hidden');
}

function highlightRotSearchResult() {
    document.querySelectorAll('[data-idx]').forEach(el => {
        const idx = parseInt(el.dataset.idx, 10);
        el.classList.toggle('selected', idx === rotSearchSelectedIdx);
    });
}

async function selectRotSearchResult(result) {
    const singerInput = document.getElementById('rotation-singer');
    const songInput = document.getElementById('rotation-song');
    const form = document.getElementById('rotation-add-form');
    const linkTargetId = form ? form.dataset.linkTargetId : null;

    // In link mode, we don't need a singer name (already exists)
    if (!linkTargetId) {
        const singer = singerInput ? singerInput.value.trim() : '';
        if (!singer) { if (singerInput) singerInput.focus(); return; }
    }

    hideRotSearchDropdown();
    showRotationIndicator('spin');

    try {
        if (linkTargetId) {
            // Link mode: link result to existing rotation entry
            const entryId = parseInt(linkTargetId);
            // Also update the song_artist text to match the linked result
            const newSongArtist = result.song_artist || songInput.value.trim();
            if (newSongArtist) {
                await fetch('/rotation/edit', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ id: entryId, song_artist: newSongArtist }),
                });
            }
            if (result.type === 'local') {
                const resp = await fetch('/rotation/link', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ id: entryId, file_path: result.path }),
                });
                const data = await resp.json();
                if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
            } else if (result.type === 'divebar' || result.type === 'youtube') {
                const body = {
                    id: entryId,
                    source: result.type,
                };
                if (result.type === 'divebar') {
                    body.file_id = result.file_id;
                    body.filename = result.filename;
                } else {
                    body.youtube_url = result.youtube_url;
                    body.filename = result.filename;
                }
                const resp = await fetch('/rotation/download-and-link', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body),
                });
                const data = await resp.json();
                if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
            }
        } else {
            // Add mode: create new rotation entry with linked result
            const singer = singerInput.value.trim();
            if (result.type === 'local') {
                const resp = await fetch('/rotation/add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        singer,
                        song_artist: result.song_artist || songInput.value.trim(),
                        file_path: result.path,
                    }),
                });
                const data = await resp.json();
                if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
            } else if (result.type === 'divebar' || result.type === 'youtube') {
                const body = {
                    singer,
                    song_artist: result.song_artist || songInput.value.trim(),
                    source: result.type,
                };
                if (result.type === 'divebar') {
                    body.file_id = result.file_id;
                    body.filename = result.filename;
                } else {
                    body.youtube_url = result.youtube_url;
                    body.filename = result.filename;
                }
                const resp = await fetch('/rotation/download-and-link', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body),
                });
                const data = await resp.json();
                if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
            } else if (result.type === 'make') {
                // Parse artist/title from query (try "Title - Artist" or "Artist - Title")
                const query = result.rawQuery || songInput.value.trim();
                const parts = query.split(/\s*-\s*/);
                let makeArtist = parts.length >= 2 ? parts[parts.length - 1] : '';
                let makeTitle = parts.length >= 2 ? parts.slice(0, -1).join(' - ') : query;
                const resp = await fetch('/rotation/make', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        singer,
                        song_artist: query,
                        artist: makeArtist,
                        title: makeTitle,
                    }),
                });
                const data = await resp.json();
                if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
            }
        }

        // Clear form, link mode, and dropdown
        hideRotSearchDropdown();
        if (linkTargetId) {
            exitLinkMode();
        } else {
            if (singerInput) singerInput.value = '';
            songInput.value = '';
            // Re-focus singer name for rapid-fire adds
            if (singerInput) singerInput.focus();
        }
        showRotationIndicator('success');
    } catch (e) {
        showRotationIndicator('error');
    }
}

function hideRotSearchDropdown() {
    const dropdown = document.getElementById('rotation-search-dropdown');
    if (dropdown) dropdown.classList.add('hidden');
    rotSearchSelectedIdx = -1;
}

function fmtDur(seconds) {
    if (!seconds) return '';
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m + ':' + String(s).padStart(2, '0');
}

function escHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

// Initialize on load
document.addEventListener('DOMContentLoaded', initRotationSearch);

async function archiveRotation() {
    if (!confirm('Archive all entries to "Past events" and start a new rotation?\n\nThis cannot be undone.')) return;
    showRotationIndicator('spin');
    try {
        const response = await fetch('/rotation/archive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const data = await response.json();
        if (!response.ok) {
            showRotationIndicator('error');
            alert('Failed to archive: ' + (data.error || 'Unknown error'));
            return;
        }
        if (data.entries) {
            rotationData = data.entries;
            renderRotation(rotationData);
        }
        showRotationIndicator('success');
    } catch (e) {
        showRotationIndicator('error');
    }
}

// --- Browser Mode ---

let browserModeActive = false;

async function toggleBrowserMode() {
    if (browserModeActive) {
        await disableBrowserMode();
    } else {
        await enableBrowserMode();
    }
}

async function enableBrowserMode(overrideUrl) {
    const urlInput = document.getElementById('browser-mode-url');
    const url = overrideUrl || urlInput.value.trim() || 'https://youtube.com';

    log('Enabling browser mode...');
    const btn = document.getElementById('browser-mode-toggle');
    btn.disabled = true;
    btn.textContent = 'Switching...';

    const data = await apiCall('/browser-mode/enable', { url });
    btn.disabled = false;
    if (data && data.success) {
        log(`Browser mode enabled — ${url}`, 'success');
    } else {
        btn.textContent = 'Enable Browser Mode';
    }
}

async function disableBrowserMode() {
    log('Disabling browser mode...');
    const btn = document.getElementById('browser-mode-toggle');
    btn.disabled = true;
    btn.textContent = 'Switching...';

    const data = await apiCall('/browser-mode/disable', {});
    btn.disabled = false;
    if (data && data.success) {
        log('Browser mode disabled — back to VLC', 'success');
    } else {
        btn.textContent = 'Disable Browser Mode';
    }
}

async function browserModeNavigate() {
    const urlInput = document.getElementById('browser-mode-url');
    const url = urlInput.value.trim();
    if (!url) return;
    const goBtn = document.getElementById('browser-mode-go');
    goBtn.disabled = true;
    goBtn.textContent = '...';
    const data = await apiCall('/browser-mode/navigate', { url });
    goBtn.disabled = false;
    goBtn.textContent = 'Go';
    if (data && data.success) {
        log(`Navigated to ${url}`, 'success');
    }
}

function updateBrowserModeUI(browserMode) {
    if (!browserMode) return;
    browserModeActive = browserMode.enabled;

    const btn = document.getElementById('browser-mode-toggle');
    const badge = document.getElementById('browser-mode-badge');
    const statusEl = document.getElementById('browser-mode-status');
    const urlInput = document.getElementById('browser-mode-url');
    const goBtn = document.getElementById('browser-mode-go');

    if (browserModeActive) {
        btn.textContent = 'Disable Browser Mode';
        btn.className = 'system-btn system-btn-danger';
        btn.disabled = false;
        badge.classList.remove('hidden');
        goBtn.classList.remove('hidden');
        const pid = browserMode.pid ? ` (PID ${browserMode.pid})` : '';
        statusEl.innerHTML = `Mode: <strong>Browser</strong>${pid} — <span class="browser-mode-url-display">${escapeHtml(browserMode.url || '')}</span>`;
    } else {
        btn.textContent = 'Enable Browser Mode';
        btn.className = 'btn-primary';
        btn.disabled = false;
        badge.classList.add('hidden');
        goBtn.classList.add('hidden');
        statusEl.innerHTML = 'Mode: <strong>VLC</strong> (default)';
    }
}

// Update modifier-hover indicators as keys change
document.addEventListener('keydown', (e) => {
    // Skip modifier-key highlighting when any rotation row is being edited
    if (document.querySelector('.rotation-editing')) return;
    const hovered = document.querySelector('.rotation-entry:hover');
    if (!hovered) return;
    if (e.ctrlKey || e.metaKey) {
        hovered.classList.remove('rotation-edit-hover');
        hovered.classList.add('rotation-delete-hover');
    } else if (e.shiftKey) {
        hovered.classList.remove('rotation-delete-hover');
        hovered.classList.add('rotation-edit-hover');
    }
});
document.addEventListener('keyup', (e) => {
    if (document.querySelector('.rotation-editing')) return;
    if (!e.ctrlKey && !e.metaKey) {
        document.querySelectorAll('.rotation-delete-hover').forEach(el => el.classList.remove('rotation-delete-hover'));
    }
    if (!e.shiftKey) {
        document.querySelectorAll('.rotation-edit-hover').forEach(el => el.classList.remove('rotation-edit-hover'));
    }
});
