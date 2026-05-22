/* Nomad KJ Control — Application Logic */

// --- Logging ---

const logArea = document.getElementById('log-area');

// --- Undo/Redo History ---

const rotationHistory = {
    undoStack: [],
    redoStack: [],
    maxSize: 10,

    pushUndo(snapshot) {
        this.undoStack.push(JSON.parse(JSON.stringify(snapshot)));
        if (this.undoStack.length > this.maxSize) this.undoStack.shift();
        this.redoStack = [];
        this.updateButtons();
    },

    async undo() {
        if (this.undoStack.length === 0) return;
        this.redoStack.push(JSON.parse(JSON.stringify(rotationData)));
        const snapshot = this.undoStack.pop();
        await this._restore(snapshot);
    },

    async redo() {
        if (this.redoStack.length === 0) return;
        this.undoStack.push(JSON.parse(JSON.stringify(rotationData)));
        const snapshot = this.redoStack.pop();
        await this._restore(snapshot);
    },

    async _restore(snapshot) {
        showRotationIndicator('spin');
        try {
            const response = await fetch('/rotation/restore', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ entries: snapshot }),
            });
            const data = await response.json();
            if (!response.ok) {
                showRotationIndicator('error');
                return;
            }
            if (data.entries) {
                rotationData = data.entries;
                renderRotation(rotationData);
                if (data.singer_stats) { renderSingerStats(data.singer_stats); }
            }
            showRotationIndicator('success');
        } catch (e) {
            showRotationIndicator('error');
        }
        this.updateButtons();
    },

    updateButtons() {
        const undoBtn = document.getElementById('rotation-undo-btn');
        const redoBtn = document.getElementById('rotation-redo-btn');
        if (undoBtn) {
            undoBtn.disabled = this.undoStack.length === 0;
            undoBtn.title = this.undoStack.length > 0
                ? `Undo (${this.undoStack.length} remaining)`
                : 'Nothing to undo';
        }
        if (redoBtn) {
            redoBtn.disabled = this.redoStack.length === 0;
            redoBtn.title = this.redoStack.length > 0
                ? `Redo (${this.redoStack.length} remaining)`
                : 'Nothing to redo';
        }
    },
};

// --- Singer pill input state ---
const singerPillInput = {
    pills: [],

    render() {
        const container = document.getElementById('singer-input-container');
        if (!container) return;
        container.querySelectorAll('.singer-pill').forEach(el => el.remove());
        const input = document.getElementById('rotation-singer');
        this.pills.forEach((name, idx) => {
            const pill = document.createElement('span');
            pill.className = 'singer-pill';
            pill.textContent = name;
            const x = document.createElement('span');
            x.className = 'singer-pill-x';
            x.textContent = '\u00d7';
            x.onclick = (e) => { e.stopPropagation(); this.removePill(idx); };
            pill.appendChild(x);
            container.insertBefore(pill, input);
        });
    },

    addPill(name) {
        const trimmed = name.trim();
        if (!trimmed) return;
        this.pills.push(trimmed);
        this.render();
    },

    removePill(idx) {
        this.pills.splice(idx, 1);
        this.render();
        document.getElementById('rotation-singer').focus();
    },

    clear() {
        this.pills = [];
        this.render();
    },

    getSingers() {
        const input = document.getElementById('rotation-singer');
        const remaining = input ? input.value.trim() : '';
        const all = [...this.pills];
        if (remaining) all.push(remaining);
        return all;
    },
};

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

// Wraps apiCall() for rotation mutations: on success, updates rotationData
// from data.entries and re-renders. Returns true on success, false on any
// error (HTTP, network, or JSON parse). apiCall() already logs the error.
async function rotationMutate(endpoint, body) {
    const data = await apiCall(endpoint, body);
    if (data === null) return false;
    if (data.entries) {
        // Pre-seed the queue snapshot for any newly-tracked download.
        // Without this, a freshly-created entry's row briefly renders as
        // "failed" — effectiveDownloadStatus treats a missing entry in
        // lastRotationDownloads as "queue item gone" — for ~2s until the
        // next /status poll catches up. Visible as a "failed" flash on
        // every DL & Link click.
        for (const e of data.entries) {
            const id = String(e.id);
            const stored = e.download_status;
            if (e.download_id
                && (stored === 'queued' || stored === 'downloading')
                && !lastRotationDownloads[id]) {
                lastRotationDownloads[id] = { status: stored, file_path: e.file_path };
            }
        }
        rotationData = data.entries;
        renderRotation(rotationData);
    }
    return true;
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
            await refreshMediaData();
            if (searchActive) {
                const q = document.getElementById('catalog-search').value.trim();
                if (q) catalogSearch(q); else clearSearch();
            } else {
                renderFolderView(applyMediaFilter(localMediaItems));
            }
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

// --- Pitch Control ---

let _currentPitch = 0;

async function changePitch(delta) {
    // delta: +1 or -1 for relative, 0 for reset
    const newPitch = delta === 0 ? 0 : Math.max(-6, Math.min(6, _currentPitch + delta));
    if (newPitch === _currentPitch && delta !== 0) return;
    const resp = await apiCall('/pitch', { semitones: newPitch });
    if (resp && resp.pitch_semitones !== undefined) {
        _currentPitch = resp.pitch_semitones;
        updatePitchDisplay(_currentPitch);
    }
}

function updatePitchDisplay(semitones) {
    _currentPitch = semitones;
    const el = document.getElementById('np-pitch');
    if (!el) return;
    el.textContent = semitones > 0 ? '+' + semitones : String(semitones);
    el.className = 'np-pitch-display' + (semitones !== 0 ? ' np-pitch-active' : '');
    const downBtn = document.getElementById('np-pitch-down');
    const upBtn = document.getElementById('np-pitch-up');
    if (downBtn) downBtn.disabled = semitones <= -6;
    if (upBtn) upBtn.disabled = semitones >= 6;
}

let _fadingOut = false;
async function fadeOut() {
    if (_fadingOut) return;
    _fadingOut = true;
    const btns = [document.getElementById('btn-fadeout'), document.getElementById('np-fadeout')];
    btns.forEach(b => { if (b) { b.textContent = 'Fading...'; b.disabled = true; } });
    log('Fading out karaoke...');
    await apiCall('/control', { action: 'fadeout' });
    // Wait for the 3s fade to finish before resetting UI
    setTimeout(() => {
        _fadingOut = false;
        btns.forEach(b => { if (b) b.textContent = 'Fade Out'; });
    }, 3500);
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

    // Renderer-aware UI: hide pitch controls on engines that don't support it,
    // show a small badge so the KJ knows which engine is active without
    // opening the modal.
    const renderer = data.renderer || null;
    if (renderer) {
        const pitchGroup = document.getElementById('np-pitch-group');
        if (pitchGroup) {
            pitchGroup.style.display = renderer.supports_pitch ? '' : 'none';
        }
        const badge = document.getElementById('np-renderer-badge');
        if (badge) {
            badge.textContent = renderer.mode === 'mpv' ? 'mpv' : 'VLC';
            badge.className = 'np-renderer-badge np-renderer-' + renderer.mode;
        }
    }

    if (isActive && data.current_playing) {
        bar.classList.remove('hidden');
        npTitle.textContent = data.current_playing;
        npTime.textContent = formatTime(data.time);
        npLength.textContent = formatTime(data.length);

        npState.textContent = state === 'playing' ? 'Playing' : 'Paused';
        npState.className = 'now-playing-state ' + (state === 'playing' ? 'state-playing' : 'state-paused');
        npPause.textContent = state === 'playing' ? 'Pause' : 'Resume';

        if (data.pitch_semitones !== undefined) {
            updatePitchDisplay(data.pitch_semitones);
        }
    } else {
        bar.classList.add('hidden');
    }
}

function updatePlaybackButtons(state) {
    const btnPause = document.getElementById('btn-pause');
    const btnRestart = document.getElementById('btn-restart');
    const btnStop = document.getElementById('btn-stop');
    const btnFadeout = document.getElementById('btn-fadeout');

    if (state === 'playing') {
        btnPause.textContent = 'Pause';
        btnPause.disabled = false;
        btnRestart.disabled = false;
        btnStop.disabled = false;
        if (!_fadingOut) btnFadeout.disabled = false;
    } else if (state === 'paused') {
        btnPause.textContent = 'Resume';
        btnPause.disabled = false;
        btnRestart.disabled = false;
        btnStop.disabled = false;
        if (!_fadingOut) btnFadeout.disabled = false;
    } else {
        btnPause.textContent = 'Pause / Resume';
        btnPause.disabled = false; // Keep enabled so user can always try
        btnRestart.disabled = true;
        btnStop.disabled = true;
        btnFadeout.disabled = true;
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

            // Reconcile rotation downloads. Any change in the rotation-linked
            // queue slice (new entry, status change, or removal) triggers a
            // rotation refetch so the stored download_status catches up. The
            // immediate re-render uses effectiveDownloadStatus() to unlock
            // action buttons before the refetch completes.
            const nextRotDls = data.rotation_downloads || {};
            const rotDlsChanged = rotationDownloadsDiffer(lastRotationDownloads, nextRotDls);
            lastRotationDownloads = nextRotDls;
            if (rotDlsChanged) {
                fetchRotation();
                renderRotation(rotationData);
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
        // Fetch AV status and renderer state in parallel
        const [avResp, rendererResp] = await Promise.all([
            fetch('/av/status'),
            fetch('/renderer'),
        ]);
        if (!avResp.ok) throw new Error('AV status fetch failed');
        const data = await avResp.json();
        const rendererData = rendererResp.ok ? await rendererResp.json() : null;
        renderAvModal(data, rendererData);
    } catch (e) {
        const errEl = document.getElementById('av-loading');
        errEl.textContent = '';
        const span = document.createElement('span');
        span.style.color = '#ef4444';
        span.textContent = `Error loading AV status: ${e.message}`;
        errEl.appendChild(span);
    }
}

function renderAvModal(data, rendererData) {
    renderAvHealthBar(data.health);
    renderAvRendererSection(rendererData);
    renderAvVideoSection(data.video);
    renderAvAudioSection(data.audio);
    renderAvBrowserAudioSection(data.audio);
    populateAvResolutionSelect(data.video);
    populateAvHdmiPcmSelect(data.audio);
    populateAvVlcDeviceSelect(data.audio);
    populateAvBrowserAudioSelect(data.audio);
    renderAvMonitorSection(data.audio_monitor);
    document.getElementById('av-loading').classList.add('hidden');
    document.getElementById('av-content').classList.remove('hidden');
}

function renderAvRendererSection(rendererData) {
    if (!rendererData) return;
    // Mark the active radio; other branches handled by the name="av-renderer" group
    const radios = document.querySelectorAll('input[name="av-renderer"]');
    radios.forEach(r => {
        r.checked = (r.value === rendererData.mode);
    });
}

async function avSetRenderer(mode) {
    try {
        const resp = await fetch('/renderer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode }),
        });
        if (resp.status === 409) {
            const err = await resp.json();
            alert(err.message || 'Stop playback before switching renderer.');
            // Revert the radio selection to the actual active mode
            avRefresh();
            return;
        }
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            alert(err.message || 'Renderer switch failed.');
            avRefresh();
            return;
        }
        const data = await resp.json();
        // Brief visual confirmation; subsequent /status polls will refresh the badge
        console.log(`Renderer switched to ${data.mode}.`);
    } catch (e) {
        alert(`Renderer switch error: ${e.message}`);
    }
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
    log(`Setting playback audio device to ${device}...`);
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

// --- Audio Monitor ---

function renderAvMonitorSection(monitorData) {
    const info = document.getElementById('av-monitor-info');
    const btn = document.getElementById('av-monitor-btn');
    const listenDiv = document.getElementById('av-monitor-listen');
    const urlEl = document.getElementById('av-monitor-url');

    if (!monitorData || !monitorData.active) {
        info.innerHTML = `
            <div class="av-info-row">
                <span class="av-info-label">Status</span>
                <span class="av-dot av-dot-off"></span>
                <span class="av-info-value">Off</span>
            </div>`;
        btn.textContent = 'Start Monitor';
        btn.onclick = () => toggleAudioMonitor(true);
        btn.disabled = false;
        listenDiv.classList.add('hidden');
    } else {
        info.innerHTML = `
            <div class="av-info-row">
                <span class="av-info-label">Status</span>
                <span class="av-dot av-dot-ok"></span>
                <span class="av-info-value">Streaming (PipeWire HDMI)</span>
            </div>`;
        btn.textContent = 'Stop Monitor';
        btn.onclick = () => toggleAudioMonitor(false);
        btn.disabled = false;
        const host = location.hostname || 'nomadpc.local';
        const port = location.port || (location.protocol === 'https:' ? '443' : '80');
        const proto = location.protocol;
        urlEl.textContent = `ffplay ${proto}//${host}:${port}/audio-monitor/stream`;
        listenDiv.classList.remove('hidden');
    }
}

async function toggleAudioMonitor(start) {
    const btn = document.getElementById('av-monitor-btn');
    btn.disabled = true;
    btn.textContent = start ? 'Starting…' : 'Stopping…';
    const endpoint = start ? '/audio-monitor/start' : '/audio-monitor/stop';
    log(start ? 'Starting audio monitor (switching to PipeWire)…' : 'Stopping audio monitor (restoring ALSA)…');
    const data = await apiCall(endpoint, {});
    if (data && data.success) {
        log(start ? 'Audio monitor started.' : 'Audio monitor stopped.', 'success');
        setTimeout(avRefresh, start ? 5000 : 4000);
    } else {
        btn.disabled = false;
        btn.textContent = start ? 'Start Monitor' : 'Stop Monitor';
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

function onOverlayMaxWidthChange() {
    const maxWidth = parseInt(document.getElementById('overlay-max-width').value) || 0;
    const alignRow = document.getElementById('overlay-text-align-row');
    if (alignRow) alignRow.classList.toggle('hidden', maxWidth <= 0);
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
    document.getElementById('overlay-font-family').value = cfg.font_family || 'sans';
    document.getElementById('overlay-bold').checked = cfg.bold != null ? cfg.bold : true;
    document.getElementById('overlay-italic').checked = cfg.italic || false;
    document.getElementById('overlay-max-width').value = cfg.max_width || 0;
    document.getElementById('overlay-text-align').value = cfg.text_align || 'left';
    document.getElementById('overlay-text-color').value = cfg.text_color || '#FFFFFF';
    document.getElementById('overlay-bg-color').value = cfg.bg_color || '#000000';
    document.getElementById('overlay-bg-opacity').value = cfg.bg_opacity != null ? cfg.bg_opacity : 0.85;
    document.getElementById('overlay-opacity-label').textContent = Math.round((cfg.bg_opacity != null ? cfg.bg_opacity : 0.85) * 100) + '%';
    document.getElementById('overlay-padding').value = cfg.padding != null ? cfg.padding : 12;
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
    onOverlayMaxWidthChange();
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
    if (type === 'static_text') {
        config.font_family = document.getElementById('overlay-font-family').value;
        config.bold = document.getElementById('overlay-bold').checked;
        config.italic = document.getElementById('overlay-italic').checked;
        config.max_width = parseInt(document.getElementById('overlay-max-width').value) || 0;
        config.text_align = document.getElementById('overlay-text-align').value;
        config.padding = parseInt(document.getElementById('overlay-padding').value) || 12;
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

// --- Wallpaper ---

function showWallpaperModal() {
    document.getElementById('wallpaper-modal').classList.remove('hidden');
    // Refresh preview with cache-bust
    const img = document.getElementById('wallpaper-preview');
    img.src = '/wallpaper?t=' + Date.now();
    document.getElementById('wallpaper-status').innerHTML = '';
    document.getElementById('wallpaper-file').value = '';
}

function hideWallpaperModal() {
    document.getElementById('wallpaper-modal').classList.add('hidden');
}

async function uploadWallpaper(input) {
    const file = input.files[0];
    if (!file) return;
    const status = document.getElementById('wallpaper-status');
    status.innerHTML = '<span style="color:#aaa">Uploading...</span>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/wallpaper', { method: 'POST', body: formData });
        const data = await resp.json();
        if (resp.ok) {
            log('Wallpaper updated: ' + file.name, 'success');
            status.innerHTML = '<span style="color:#4caf50">Wallpaper updated!</span>';
            // Refresh preview
            document.getElementById('wallpaper-preview').src = '/wallpaper?t=' + Date.now();
        } else {
            status.innerHTML = '<span style="color:#f44336">' + (data.error || 'Upload failed') + '</span>';
        }
    } catch (e) {
        status.innerHTML = '<span style="color:#f44336">Upload failed</span>';
    }
    input.value = '';
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
        showSystemOverlay('Code updated. Restarting service...\nVLC keeps playing — no interruption.');
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

    if (localStorage.getItem('kj-singer-stats-hidden') === '1') {
        const statsList = document.getElementById('singer-stats-list');
        const statsBtn = document.querySelector('.singer-stats-toggle');
        if (statsList) statsList.classList.add('hidden');
        if (statsBtn) statsBtn.textContent = 'Show';
    }

    // Singer pill input keydown handlers
    const singerInput = document.getElementById('rotation-singer');
    if (singerInput) {
        singerInput.addEventListener('keydown', (e) => {
            const val = singerInput.value;

            // Tab, comma, or & with text -> create pill
            if ((e.key === 'Tab' || e.key === ',' || e.key === '&') && val.trim()) {
                e.preventDefault();
                singerPillInput.addPill(val);
                singerInput.value = '';
                return;
            }

            // Backspace on empty input -> remove last pill
            if (e.key === 'Backspace' && !val && singerPillInput.pills.length > 0) {
                singerPillInput.removePill(singerPillInput.pills.length - 1);
                return;
            }

            // Enter -> move focus to song field
            if (e.key === 'Enter') {
                e.preventDefault();
                document.getElementById('rotation-song').focus();
            }
        });
    }
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
let singerStatsData = [];
// Map of rotation entry id (as string) → {status, progress, file_path}, mirroring
// download_queue items that are linked to rotation entries. Refreshed by the
// status poll. Used to reconcile stale entry.download_status when the worker or
// user action has already removed/errored the underlying queue item.
let lastRotationDownloads = {};

// Compute the *effective* download_status for a rotation entry, reconciling
// the rotation row's stored status against the live download queue. This
// protects the UI from stale 'queued'/'downloading' rows whose backing queue
// item has vanished or errored — without it the row's action buttons stay
// hidden forever, blocking cancel/retry.
function effectiveDownloadStatus(entry) {
    const stored = entry.download_status;
    if (!stored || stored === 'complete' || stored === 'failed') return stored;
    // stored is 'queued' or 'downloading' — live queue is the source of truth.
    const dl = lastRotationDownloads[String(entry.id)];
    if (!dl) return 'failed';  // Queue item gone (cancelled/dismissed)
    switch (dl.status) {
        case 'queued':      return 'queued';
        case 'downloading': return 'downloading';
        case 'error':       return 'failed';
        case 'completed':   return 'complete';
        default:            return stored;
    }
}

// Shallow compare two rotation_downloads snapshots by entry id + status, to
// detect transitions that warrant a rotation refetch (progress %, ignored).
function rotationDownloadsDiffer(a, b) {
    const aKeys = Object.keys(a || {});
    const bKeys = Object.keys(b || {});
    if (aKeys.length !== bKeys.length) return true;
    for (const k of aKeys) {
        if (!(k in b)) return true;
        if ((a[k] && a[k].status) !== (b[k] && b[k].status)) return true;
    }
    return false;
}

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
        if (data.singer_stats) { renderSingerStats(data.singer_stats); }
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
        handle.textContent = '\u2807';  // ⠇ braille 6-dot grip
        handle.title = 'Drag to reorder this entry in the queue';
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

        // Singer name(s)
        let singers_parsed = null;
        if (entry.singers_json) {
            try { singers_parsed = JSON.parse(entry.singers_json); } catch (e) { /* ignore */ }
        }

        const song = document.createElement('span');
        song.className = 'rotation-song rotation-copyable';
        song.textContent = entry.song_artist || '';
        song.title = 'Click to copy \u2022 Shift+click to edit';
        song.onclick = (e) => { if (!e.shiftKey && !e.ctrlKey && !e.metaKey) copyRotationText(song); };

        row.appendChild(handle);
        info.appendChild(num);

        if (singers_parsed && singers_parsed.length > 1) {
            // Multi-singer: render individual pills
            singers_parsed.forEach((s) => {
                const sp = document.createElement('span');
                sp.className = 'rotation-singer-pill rotation-copyable';
                sp.textContent = s;
                sp.title = 'Click to copy \u2022 Shift+click to edit';
                sp.onclick = (ev) => { if (!ev.shiftKey && !ev.ctrlKey && !ev.metaKey) copyRotationText(sp); };
                info.appendChild(sp);
            });
        } else {
            // Single singer or legacy: plain text (unchanged)
            const name = document.createElement('span');
            name.className = 'rotation-name rotation-copyable';
            name.textContent = entry.singer;
            name.title = 'Click to copy \u2022 Shift+click to edit';
            name.onclick = (ev) => { if (!ev.shiftKey && !ev.ctrlKey && !ev.metaKey) copyRotationText(name); };
            info.appendChild(name);
        }
        const pill = document.createElement('span');
        pill.className = 'rotation-songs-pill';
        const sung = entry.songs_sung || 0;
        if (sung === 0) {
            pill.classList.add('pill-new');
            pill.textContent = 'NEW';
            pill.title = 'Hasn\u2019t sung yet tonight \u2014 prioritise this singer';
        } else if (sung === 1) {
            pill.classList.add('pill-once');
            pill.textContent = '\u00d71';
            pill.title = '1 song sung tonight';
        } else if (sung <= 4) {
            pill.classList.add('pill-few');
            pill.textContent = '\u00d7' + sung;
            pill.title = sung + ' songs sung tonight';
        } else {
            pill.classList.add('pill-many');
            pill.textContent = '\u00d7' + sung;
            pill.title = sung + ' songs sung tonight';
        }
        info.appendChild(pill);
        if (entry.paid) {
            const heart = document.createElement('span');
            heart.className = 'rotation-paid-heart';
            heart.textContent = ' ♥';
            heart.title = 'This singer tipped tonight \u2014 paid priority';
            info.appendChild(heart);
        }
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
            est.title = 'Estimated time this singer will perform (based on songs ahead in queue)';
            info.appendChild(est);
        }

        const badge = document.createElement('span');
        badge.className = 'rotation-badge';
        if (statusLower.includes('singing') || statusLower === 'now singing') {
            badge.textContent = 'NOW';
            badge.classList.add('badge-now');
            badge.title = 'Currently singing';
        } else if (statusLower.includes('next')) {
            badge.textContent = 'NEXT';
            badge.classList.add('badge-next');
            badge.title = 'Up next \u2014 get ready!';
        } else if (statusLower === 'waiting') {
            badge.textContent = 'WAITING';
            badge.classList.add('badge-waiting');
            badge.title = 'Waiting in queue';
        } else if (statusLower.includes('being made')) {
            badge.textContent = 'MAKING';
            badge.classList.add('badge-wip');
            badge.title = 'Karaoke video being generated';
        } else if (statusLower.includes('on hold') || statusLower.includes('brb')) {
            badge.textContent = 'BRB';
            badge.classList.add('badge-onhold');
            badge.title = 'Singer stepped away \u2014 will be skipped until they return';
        } else if (statusLower === 'skipped') {
            badge.textContent = 'SKIP';
            badge.classList.add('badge-skipped');
            badge.title = 'Skipped this round';
        }
        if (badge.textContent) info.appendChild(badge);

        // Preparation status badge
        const prepBadge = document.createElement('span');
        prepBadge.className = 'rotation-prep-badge';
        const effDlStatus = effectiveDownloadStatus(entry);
        if (entry.file_path) {
            prepBadge.textContent = 'READY';
            prepBadge.classList.add('prep-ready');
            prepBadge.title = 'Song file linked and ready to play';
        } else if (effDlStatus === 'queued' || effDlStatus === 'downloading') {
            prepBadge.textContent = 'DOWNLOADING';
            prepBadge.classList.add(entry.download_source === 'youtube' ? 'prep-downloading-orange' : 'prep-downloading-green');
            prepBadge.title = 'Song is downloading from ' + (entry.download_source || 'source') + ' \u2014 will be ready soon';
        } else if (effDlStatus === 'failed') {
            prepBadge.textContent = 'FAILED';
            prepBadge.classList.add('prep-failed');
            prepBadge.title = 'Download failed \u2014 click the link button to try again';
        } else if (entry.url_fallback) {
            prepBadge.textContent = 'URL';
            prepBadge.classList.add('prep-url');
            prepBadge.title = 'Will play via browser mode (no local file)';
        } else if (entry.gen_status === 'processing') {
            prepBadge.textContent = 'MAKING';
            prepBadge.classList.add('prep-making');
            prepBadge.title = 'Custom karaoke video being generated \u2014 check back soon';
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
            prepBadge.title = 'Karaoke video rendering \u2014 almost ready';
        } else {
            prepBadge.textContent = 'UNLINKED';
            prepBadge.classList.add('prep-unlinked');
            prepBadge.title = 'No song file linked \u2014 use the link button to search and attach a song';
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
        } else if (effDlStatus !== 'queued' && effDlStatus !== 'downloading') {
            // No playable source and no in-flight download — offer link.
            // Covers the stale-'complete' case after unlinking a prior
            // successful download (download_status lingers on the row).
            const linkBtn = document.createElement('button');
            linkBtn.className = 'rotation-btn rotation-btn-link';
            linkBtn.textContent = '\uD83D\uDD17';  // 🔗
            linkBtn.title = effDlStatus === 'failed'
                ? 'Download failed \u2014 search again and link a different file'
                : 'Search for a matching song file and link it to this entry';
            linkBtn.onclick = () => openLinkSearch(entry.id, entry.song_artist);
            actions.appendChild(linkBtn);
        } else if (effDlStatus === 'queued' && entry.download_id) {
            // Cancel button for queued-but-not-yet-active downloads. Can't
            // cancel 'downloading' — worker has the file open. The backend
            // will clear the rotation entry's download fields after cancel.
            const cancelBtn = document.createElement('button');
            cancelBtn.className = 'rotation-btn rotation-btn-cancel';
            cancelBtn.textContent = '\u2715';  // ✕
            cancelBtn.title = 'Cancel this queued download';
            cancelBtn.onclick = async () => {
                try {
                    await apiCall('/download/cancel', { id: entry.download_id });
                    await fetchRotation();
                } catch (e) { showRotationIndicator('error'); }
            };
            actions.appendChild(cancelBtn);
        }

        if (!statusLower.includes('singing') && statusLower !== 'now singing') {
            const singBtn = document.createElement('button');
            singBtn.className = 'rotation-btn rotation-btn-sing';
            singBtn.textContent = 'Singing';
            singBtn.title = 'Mark this singer as currently performing';
            singBtn.onclick = () => updateRotationStatus(entry.id, 'Now Singing');
            actions.appendChild(singBtn);
        }

        const doneBtn = document.createElement('button');
        doneBtn.className = 'rotation-btn rotation-btn-done';
        doneBtn.textContent = 'Done';
        doneBtn.title = 'Song finished \u2014 mark as done and credit the singer';
        doneBtn.onclick = () => updateRotationStatus(entry.id, 'Done');
        actions.appendChild(doneBtn);

        if (!statusLower.includes('next')) {
            const nextBtn = document.createElement('button');
            nextBtn.className = 'rotation-btn rotation-btn-next';
            nextBtn.textContent = 'Next';
            nextBtn.title = 'Mark as up next \u2014 give them a heads up to get ready';
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
                    rotationHistory.pushUndo(rotationData);
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
            // Add paid toggle
            const paidSep = document.createElement('div');
            paidSep.className = 'rotation-dropdown-sep';
            dropdown.appendChild(paidSep);
            const paidItem = document.createElement('button');
            paidItem.className = 'rotation-dropdown-item';
            paidItem.textContent = entry.paid ? 'Remove Paid ♥' : 'Mark as Paid ♥';
            paidItem.onclick = async (ev) => {
                ev.stopPropagation();
                dropdown.remove();
                try {
                    const resp = await fetch('/rotation/set-paid', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ id: entry.id, paid: !entry.paid }),
                    });
                    if (!resp.ok) throw new Error('Failed to update paid status');
                    const data = await resp.json();
                    if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
                    showRotationIndicator('success');
                } catch (err) {
                    showRotationIndicator('error');
                }
            };
            dropdown.appendChild(paidItem);
            row.appendChild(dropdown);
            const close = () => { dropdown.remove(); document.removeEventListener('click', close); };
            setTimeout(() => document.addEventListener('click', close), 0);
        };
        actions.appendChild(moreBtn);

        // Edit pencil button
        const editBtn = document.createElement('button');
        editBtn.className = 'rotation-btn rotation-btn-edit';
        editBtn.innerHTML = '&#9998;';
        editBtn.title = 'Edit singer name or song (Shift+click row for quick edit)';
        editBtn.onclick = (e) => {
            e.stopPropagation();
            enterRotationEditMode(row, entry);
        };
        actions.appendChild(editBtn);

        row.appendChild(info);
        row.appendChild(actions);

        // File-path row (always rendered; visibility toggled by
        // .show-file-paths class on parent #rotation-list — see
        // toggleRotationFilePaths). Helps the KJ scan for entries
        // that need unlinking + re-linking with a better version.
        const pathRow = document.createElement('div');
        pathRow.className = 'rotation-file-path';
        if (entry.file_path) {
            const parts = entry.file_path.split('/');
            const basename = parts[parts.length - 1];
            pathRow.textContent = basename;
            pathRow.title = entry.file_path;
        } else {
            pathRow.classList.add('rotation-file-path-unlinked');
            pathRow.textContent = '(no file linked)';
        }
        row.appendChild(pathRow);

        list.appendChild(row);
    });
}

function toggleRotationFilePaths() {
    const list = document.getElementById('rotation-list');
    const btn = document.getElementById('rotation-paths-btn');
    if (!list) return;
    const showing = list.classList.toggle('show-file-paths');
    if (showing) {
        localStorage.setItem('kj-rotation-show-paths', '1');
        if (btn) btn.classList.add('rotation-paths-btn-active');
    } else {
        localStorage.removeItem('kj-rotation-show-paths');
        if (btn) btn.classList.remove('rotation-paths-btn-active');
    }
}

// Apply persisted file-paths toggle state once #rotation-list exists.
(function initRotationFilePathsToggle() {
    const apply = () => {
        const list = document.getElementById('rotation-list');
        const btn = document.getElementById('rotation-paths-btn');
        if (!list) return;
        if (localStorage.getItem('kj-rotation-show-paths') === '1') {
            list.classList.add('show-file-paths');
            if (btn) btn.classList.add('rotation-paths-btn-active');
        }
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', apply);
    } else {
        apply();
    }
})();

// --- Singer Stats Panel ---

function toggleSingerStats() {
    const list = document.getElementById('singer-stats-list');
    const btn = document.querySelector('.singer-stats-toggle');
    if (!list || !btn) return;
    if (list.classList.contains('hidden')) {
        list.classList.remove('hidden');
        btn.textContent = 'Hide';
        localStorage.removeItem('kj-singer-stats-hidden');
    } else {
        list.classList.add('hidden');
        btn.textContent = 'Show';
        localStorage.setItem('kj-singer-stats-hidden', '1');
    }
}

function renderSingerStats(stats) {
    const list = document.getElementById('singer-stats-list');
    if (!list) return;
    if (list.querySelector('.singer-editing')) return;

    singerStatsData = stats || [];
    list.innerHTML = '';

    const sections = {
        active: [],
        done: [],
        gone: [],
    };
    for (const singer of singerStatsData) {
        if (singer.status === 'left') sections.gone.push(singer);
        else if (singer.status === 'done') sections.done.push(singer);
        else sections.active.push(singer);
    }

    renderSingerSection(list, 'active', 'Active', sections.active, false);
    renderSingerSection(list, 'done', 'Done', sections.done, true);
    renderSingerSection(list, 'gone', 'Gone', sections.gone, true);
}

function renderSingerSection(container, key, label, singers, collapsedByDefault) {
    if (singers.length === 0) return;

    const storageKey = 'kj-singers-' + key + '-collapsed';
    const stored = localStorage.getItem(storageKey);
    const collapsed = stored === null ? collapsedByDefault : (stored === '1');

    const section = document.createElement('div');
    section.className = 'singer-section singer-section-' + key;
    if (collapsed) section.classList.add('collapsed');

    const header = document.createElement('div');
    header.className = 'singer-section-header';
    header.innerHTML = '<span class="singer-section-caret">\u25B8</span> '
        + '<span class="singer-section-label">' + label + '</span> '
        + '<span class="singer-section-count">(' + singers.length + ')</span>';
    header.onclick = () => {
        const isCollapsed = section.classList.toggle('collapsed');
        localStorage.setItem(storageKey, isCollapsed ? '1' : '0');
    };
    section.appendChild(header);

    const body = document.createElement('div');
    body.className = 'singer-section-body';
    for (const singer of singers) {
        body.appendChild(buildSingerRow(singer));
    }
    section.appendChild(body);
    container.appendChild(section);
}

function buildSingerRow(singer) {
    const row = document.createElement('div');
    row.className = 'singer-stats-row';
    if (singer.status === 'brb') row.classList.add('singer-brb');
    if (singer.status === 'left') row.classList.add('singer-left');

    const info = document.createElement('div');
    info.className = 'singer-stats-info';

    const name = document.createElement('span');
    name.className = 'singer-stats-name';
    name.textContent = singer.name;
    info.appendChild(name);

    if (singer.has_tipped) {
        const tip = document.createElement('span');
        tip.className = 'singer-stats-tip';
        tip.textContent = '\u2764\uFE0F';
        info.appendChild(tip);
    }

    if (singer.first_added) {
        const joined = document.createElement('span');
        joined.className = 'singer-stats-label';
        const added = new Date(singer.first_added.replace(' ', 'T'));
        const mins = Math.round((Date.now() - added.getTime()) / 60000);
        const ago = mins < 60 ? mins + ' mins ago' : Math.floor(mins / 60) + 'h ' + (mins % 60) + 'm ago';
        joined.innerHTML = '<span class="singer-label-key">Joined:</span> ' + ago;
        info.appendChild(joined);
    }

    const sung = document.createElement('span');
    sung.className = 'singer-stats-label';
    const sungCount = singer.entries_sung;
    let pillClass = 'pill-new';
    if (sungCount === 1) pillClass = 'pill-once';
    else if (sungCount >= 2 && sungCount <= 4) pillClass = 'pill-few';
    else if (sungCount >= 5) pillClass = 'pill-many';
    sung.innerHTML = '<span class="singer-label-key">Sung:</span> <span class="singer-sung-pill ' + pillClass + '">' + sungCount + '</span>';
    info.appendChild(sung);

    if (singer.entries_waiting > 0) {
        const queued = document.createElement('span');
        queued.className = 'singer-stats-label';
        queued.innerHTML = '<span class="singer-label-key">Queued:</span> ' + singer.entries_waiting;
        info.appendChild(queued);
    }

    if (singer.entries_waiting > 0) {
        const singerLower = singer.name.toLowerCase();
        const nextEntry = rotationData.find(e => {
            if (e.singers_json) {
                try {
                    const names = JSON.parse(e.singers_json);
                    return names.some(n => n.trim().toLowerCase() === singerLower);
                } catch (err) { return false; }
            }
            return e.singer.toLowerCase() === singerLower;
        });
        if (nextEntry && nextEntry.estimated_time) {
            const est = document.createElement('span');
            est.className = 'singer-stats-label';
            est.innerHTML = '<span class="singer-label-key">Next:</span> ~' + nextEntry.estimated_time;
            info.appendChild(est);
        }
    }

    row.appendChild(info);

    const actions = document.createElement('div');
    actions.className = 'singer-stats-actions';
    buildSingerActions(actions, singer, row);
    row.appendChild(actions);

    return row;
}

function buildSingerActions(actions, singer, row) {
    const songsBtn = document.createElement('button');
    songsBtn.className = 'singer-stats-btn';
    songsBtn.textContent = 'Songs';
    songsBtn.title = 'Show all songs this singer has queued or sung tonight';
    songsBtn.onclick = () => toggleSingerSongs(row, singer);
    actions.appendChild(songsBtn);

    if (singer.status === 'left') {
        const restoreBtn = document.createElement('button');
        restoreBtn.className = 'singer-stats-btn';
        restoreBtn.textContent = 'Restore';
        restoreBtn.title = 'Bring this singer back \u2014 restore their songs to the queue';
        restoreBtn.onclick = () => singerAction('restore', { name: singer.name });
        actions.appendChild(restoreBtn);
        return;
    }

    // Edit + Merge + Split available on both active and done
    const editBtn = document.createElement('button');
    editBtn.className = 'singer-stats-btn';
    editBtn.textContent = 'Edit';
    editBtn.title = 'Rename this singer (fixes typos across all their entries)';
    editBtn.onclick = () => enterSingerEditMode(row, singer);
    actions.appendChild(editBtn);

    const mergeBtn = document.createElement('button');
    mergeBtn.className = 'singer-stats-btn';
    mergeBtn.textContent = 'Merge';
    mergeBtn.title = 'Merge this singer into another \u2014 use when the same person was added under two different names';
    mergeBtn.onclick = (ev) => showMergeDropdown(ev, singer);
    actions.appendChild(mergeBtn);

    const splitBtn = document.createElement('button');
    splitBtn.className = 'singer-stats-btn';
    splitBtn.textContent = 'Split';
    splitBtn.title = 'Split this singer \u2014 reassign some of their songs to a different name';
    splitBtn.onclick = () => openSplitModal(singer);
    actions.appendChild(splitBtn);

    // BRB only meaningful when there's an active queue
    if (singer.status !== 'done') {
        const brbBtn = document.createElement('button');
        brbBtn.className = 'singer-stats-btn';
        brbBtn.textContent = singer.status === 'brb' ? 'Back' : 'BRB';
        brbBtn.title = singer.status === 'brb'
            ? 'Singer is back \u2014 restore their songs to the active queue'
            : 'Singer stepped away \u2014 hold all their songs until they return';
        brbBtn.onclick = () => singerAction('brb', { name: singer.name, brb: singer.status !== 'brb' });
        actions.appendChild(brbBtn);
    }

    const leftBtn = document.createElement('button');
    leftBtn.className = 'singer-stats-btn';
    leftBtn.textContent = 'Left';
    leftBtn.title = 'Mark this singer as having left \u2014 hides them from the active list (can be restored).';
    leftBtn.onclick = () => singerAction('remove', { name: singer.name });
    actions.appendChild(leftBtn);
}

function toggleSingerSongs(row, singer) {
    // Toggle: if already open, close it
    const existing = row.nextElementSibling;
    if (existing && existing.classList.contains('singer-songs-panel')
        && existing.dataset.singer === singer.name) {
        existing.remove();
        return;
    }
    // Close any other open panel
    document.querySelectorAll('.singer-songs-panel').forEach(p => p.remove());

    const panel = document.createElement('div');
    panel.className = 'singer-songs-panel';
    panel.dataset.singer = singer.name;

    const entries = singer.entries || [];
    if (entries.length === 0) {
        panel.innerHTML = '<div class="singer-songs-empty">No songs recorded for this singer.</div>';
    } else {
        const table = document.createElement('table');
        table.className = 'singer-songs-table';
        const tbody = document.createElement('tbody');
        for (const entry of entries) {
            const tr = document.createElement('tr');

            const songCell = document.createElement('td');
            songCell.className = 'singer-songs-song';
            songCell.textContent = entry.song_artist || '(no song)';
            tr.appendChild(songCell);

            const statusCell = document.createElement('td');
            statusCell.className = 'singer-songs-status';
            const statusLower = (entry.status || '').toLowerCase();
            const pillClass = statusLower.includes('done') ? 'status-done'
                : statusLower.includes('left') ? 'status-left'
                : statusLower.includes('hold') ? 'status-brb'
                : statusLower.includes('now') ? 'status-singing'
                : statusLower.includes('next') ? 'status-next'
                : 'status-waiting';
            statusCell.innerHTML = '<span class="singer-songs-status-pill ' + pillClass + '">'
                + (entry.status || 'Waiting') + '</span>';
            tr.appendChild(statusCell);

            const timeCell = document.createElement('td');
            timeCell.className = 'singer-songs-time';
            if (entry.created_at) {
                const added = new Date(entry.created_at.replace(' ', 'T'));
                const mins = Math.round((Date.now() - added.getTime()) / 60000);
                timeCell.textContent = mins < 60 ? mins + 'm ago' : Math.floor(mins / 60) + 'h ' + (mins % 60) + 'm ago';
            }
            tr.appendChild(timeCell);

            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        panel.appendChild(table);
    }

    row.parentNode.insertBefore(panel, row.nextSibling);
}
function openSplitModal(singer) {
    document.querySelectorAll('.singer-split-modal-backdrop').forEach(d => d.remove());

    const entries = singer.entries || [];
    if (entries.length === 0) {
        alert('This singer has no entries to split.');
        return;
    }

    const backdrop = document.createElement('div');
    backdrop.className = 'singer-split-modal-backdrop';
    backdrop.onclick = (ev) => { if (ev.target === backdrop) backdrop.remove(); };

    const modal = document.createElement('div');
    modal.className = 'singer-split-modal';

    const heading = document.createElement('h3');
    heading.textContent = 'Split "' + singer.name + '"';
    modal.appendChild(heading);

    const help = document.createElement('p');
    help.className = 'singer-split-help';
    help.textContent = 'Pick the entries that actually belong to a different person, then give that person a name.';
    modal.appendChild(help);

    // Entry checkboxes
    const listWrap = document.createElement('div');
    listWrap.className = 'singer-split-entries';
    for (const entry of entries) {
        const label = document.createElement('label');
        label.className = 'singer-split-entry';

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = String(entry.id);
        label.appendChild(cb);

        const song = document.createElement('span');
        song.className = 'singer-split-entry-song';
        song.textContent = entry.song_artist || '(no song)';
        label.appendChild(song);

        const status = document.createElement('span');
        status.className = 'singer-split-entry-status';
        status.textContent = entry.status || 'Waiting';
        label.appendChild(status);

        listWrap.appendChild(label);
    }
    modal.appendChild(listWrap);

    // Reassign-to pick
    const formRow = document.createElement('div');
    formRow.className = 'singer-split-form-row';
    formRow.innerHTML = '<span class="singer-split-form-label">Reassign selected to:</span>';
    modal.appendChild(formRow);

    const modeWrap = document.createElement('div');
    modeWrap.className = 'singer-split-mode';

    const newLabel = document.createElement('label');
    const newRadio = document.createElement('input');
    newRadio.type = 'radio';
    newRadio.name = 'split-mode';
    newRadio.value = 'new';
    newRadio.checked = true;
    newLabel.appendChild(newRadio);
    newLabel.appendChild(document.createTextNode(' New name: '));
    const newInput = document.createElement('input');
    newInput.type = 'text';
    newInput.className = 'singer-split-new-input';
    newInput.placeholder = singer.name + ' P';
    newLabel.appendChild(newInput);
    modeWrap.appendChild(newLabel);

    const existingLabel = document.createElement('label');
    const existingRadio = document.createElement('input');
    existingRadio.type = 'radio';
    existingRadio.name = 'split-mode';
    existingRadio.value = 'existing';
    existingLabel.appendChild(existingRadio);
    existingLabel.appendChild(document.createTextNode(' Existing singer: '));
    const existingSelect = document.createElement('select');
    existingSelect.className = 'singer-split-existing-select';
    const others = (singerStatsData || [])
        .filter(s => s.name.toLowerCase() !== singer.name.toLowerCase())
        .map(s => s.name);
    if (others.length === 0) {
        existingRadio.disabled = true;
    }
    for (const other of others) {
        const opt = document.createElement('option');
        opt.value = other;
        opt.textContent = other;
        existingSelect.appendChild(opt);
    }
    existingLabel.appendChild(existingSelect);
    modeWrap.appendChild(existingLabel);

    modal.appendChild(modeWrap);

    // Buttons
    const buttons = document.createElement('div');
    buttons.className = 'singer-split-buttons';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'singer-stats-btn';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = () => backdrop.remove();
    buttons.appendChild(cancelBtn);

    const splitBtn = document.createElement('button');
    splitBtn.className = 'singer-stats-btn singer-split-confirm';
    splitBtn.textContent = 'Split';
    splitBtn.onclick = async () => {
        const checkedIds = Array.from(listWrap.querySelectorAll('input[type=checkbox]:checked'))
            .map(cb => parseInt(cb.value, 10));
        if (checkedIds.length === 0) {
            alert('Select at least one entry to reassign.');
            return;
        }
        const mode = modeWrap.querySelector('input[name=split-mode]:checked').value;
        const newName = mode === 'new' ? newInput.value.trim() : existingSelect.value;
        if (!newName) {
            alert('Enter a new name.');
            return;
        }
        if (newName.toLowerCase() === singer.name.toLowerCase()) {
            alert('New name must differ from the original.');
            return;
        }
        backdrop.remove();
        await singerAction('split', {
            source_name: singer.name,
            new_name: newName,
            entry_ids: checkedIds,
        });
    };
    buttons.appendChild(splitBtn);

    modal.appendChild(buttons);

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    newInput.focus();
}

async function singerAction(action, data) {
    rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');
    try {
        const resp = await fetch('/rotation/singer/' + action, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const result = await resp.json();
        if (!resp.ok) { showRotationIndicator('error'); return; }
        // Clear editing state so renderSingerStats doesn't skip the re-render
        const editingRow = document.querySelector('.singer-editing');
        if (editingRow) editingRow.classList.remove('singer-editing');
        if (result.entries) { rotationData = result.entries; renderRotation(rotationData); }
        if (result.singer_stats) { renderSingerStats(result.singer_stats); }
        showRotationIndicator('success');
    } catch (e) {
        showRotationIndicator('error');
    }
}

function enterSingerEditMode(row, singer) {
    const info = row.querySelector('.singer-stats-info');
    const actions = row.querySelector('.singer-stats-actions');
    const origInfoHTML = info.innerHTML;
    const origActionsHTML = actions.innerHTML;

    row.classList.add('singer-editing');
    info.innerHTML = '';
    const input = document.createElement('input');
    input.type = 'text';
    input.value = singer.name;
    input.className = 'rotation-edit-input';
    input.style.flex = '1';
    info.appendChild(input);

    actions.innerHTML = '';
    const saveBtn = document.createElement('button');
    saveBtn.className = 'singer-stats-btn';
    saveBtn.textContent = 'Save';
    saveBtn.onclick = async () => {
        const newName = input.value.trim();
        if (!newName || newName === singer.name) {
            row.classList.remove('singer-editing');
            info.innerHTML = origInfoHTML;
            actions.innerHTML = origActionsHTML;
            return;
        }
        row.classList.remove('singer-editing');
        await singerAction('rename', { old_name: singer.name, new_name: newName });
    };
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'singer-stats-btn';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = () => {
        row.classList.remove('singer-editing');
        info.innerHTML = origInfoHTML;
        actions.innerHTML = origActionsHTML;
    };
    actions.appendChild(saveBtn);
    actions.appendChild(cancelBtn);
    input.focus();
    input.select();
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') saveBtn.click();
        if (e.key === 'Escape') cancelBtn.click();
    });
}

function showMergeDropdown(ev, singer) {
    document.querySelectorAll('.singer-merge-dropdown').forEach(d => d.remove());

    const others = singerStatsData
        .filter(s => s.name.toLowerCase() !== singer.name.toLowerCase() && s.status !== 'done')
        .map(s => s.name);

    if (others.length === 0) return;

    const dropdown = document.createElement('div');
    dropdown.className = 'singer-merge-dropdown';
    others.forEach(targetName => {
        const opt = document.createElement('button');
        opt.className = 'singer-merge-option';
        opt.textContent = targetName;
        opt.onclick = () => {
            dropdown.remove();
            singerAction('merge', { source_name: singer.name, target_name: targetName });
        };
        dropdown.appendChild(opt);
    });

    const btn = ev.currentTarget;
    const rect = btn.getBoundingClientRect();
    dropdown.style.top = (rect.bottom + 2) + 'px';
    dropdown.style.left = rect.left + 'px';
    document.body.appendChild(dropdown);

    const close = (e) => {
        if (!dropdown.contains(e.target)) {
            dropdown.remove();
            document.removeEventListener('click', close);
        }
    };
    setTimeout(() => document.addEventListener('click', close), 0);
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

    // --- Edit pill state (local to this edit session) ---
    let editPills = [];
    if (entry.singers_json) {
        try { editPills = JSON.parse(entry.singers_json); } catch (e) { editPills = [entry.singer]; }
    } else {
        editPills = [entry.singer];
    }

    function renderEditPills() {
        container.querySelectorAll('.singer-pill').forEach(el => el.remove());
        editPills.forEach((name, idx) => {
            const pill = document.createElement('span');
            pill.className = 'singer-pill';
            pill.textContent = name;
            const x = document.createElement('span');
            x.className = 'singer-pill-x';
            x.textContent = '\u00d7';
            x.onclick = (ev) => {
                ev.stopPropagation();
                editPills.splice(idx, 1);
                renderEditPills();
                singerInput.focus();
            };
            pill.appendChild(x);
            container.insertBefore(pill, singerInput);
        });
    }

    function getEditSingers() {
        const remaining = singerInput.value.trim();
        const all = [...editPills];
        if (remaining) all.push(remaining);
        return all;
    }

    // Replace info with pill input + song input
    info.innerHTML = '';
    const container = document.createElement('div');
    container.className = 'singer-input-container';
    container.onclick = () => singerInput.focus();

    const singerInput = document.createElement('input');
    singerInput.type = 'text';
    singerInput.className = 'rotation-edit-singer';
    singerInput.placeholder = 'Singer name';
    singerInput.autocomplete = 'off';
    container.appendChild(singerInput);

    // Clear pre-populated pills, render them inside container
    renderEditPills();

    const songInput = document.createElement('input');
    songInput.type = 'text';
    songInput.className = 'rotation-edit-input rotation-edit-song';
    songInput.value = entry.song_artist || '';
    songInput.placeholder = 'Song & Artist';

    info.appendChild(container);
    info.appendChild(songInput);

    // Replace actions with save/cancel/delete
    actions.innerHTML = '';
    const saveBtn = document.createElement('button');
    saveBtn.className = 'rotation-btn rotation-btn-save';
    saveBtn.textContent = 'Save';
    saveBtn.onclick = (e) => {
        e.stopPropagation();
        const singers = getEditSingers();
        if (singers.length === 0) { singerInput.focus(); return; }
        const newSong = songInput.value.trim();
        saveRotationEdit(entry.id, singers, newSong);
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

    // Stop all events from bubbling out of edit inputs
    [singerInput, songInput].forEach(input => {
        input.addEventListener('click', (e) => e.stopPropagation());
        input.addEventListener('keyup', (e) => e.stopPropagation());
    });

    // Singer input: Tab/comma/& create pills, Backspace removes, Enter moves to song
    singerInput.addEventListener('keydown', (e) => {
        e.stopPropagation();
        const val = singerInput.value;
        if ((e.key === 'Tab' || e.key === ',' || e.key === '&') && val.trim()) {
            e.preventDefault();
            editPills.push(val.trim());
            singerInput.value = '';
            renderEditPills();
            return;
        }
        if (e.key === 'Backspace' && !val && editPills.length > 0) {
            editPills.pop();
            renderEditPills();
            return;
        }
        if (e.key === 'Enter') { e.preventDefault(); songInput.focus(); songInput.select(); }
        else if (e.key === 'Escape') { cancelBtn.click(); }
    });
    songInput.addEventListener('keydown', (e) => {
        e.stopPropagation();
        if (e.key === 'Enter') { saveBtn.click(); }
        else if (e.key === 'Escape') { cancelBtn.click(); }
    });
}

async function saveRotationEdit(entryId, singers, songArtist) {
    rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');
    try {
        const response = await fetch('/rotation/edit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: entryId, singers, song_artist: songArtist })
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
    rotationHistory.pushUndo(rotationData);
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
    rotationHistory.pushUndo(rotationData);
    // Mark this entry as singing
    await updateRotationStatus(entry.id, 'Now Singing', { skipUndo: true });
    // Mark the next entry as up next (if there is one)
    const nextEntry = entries[idx + 1];
    if (nextEntry) {
        await updateRotationStatus(nextEntry.id, 'Up Next', { skipUndo: true });
    }
}

async function updateRotationStatus(entryId, status, { skipUndo = false } = {}) {
    if (!skipUndo) rotationHistory.pushUndo(rotationData);
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
    rotationHistory.pushUndo(rotationData);
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
    const singers = singerPillInput.getSingers();
    const songArtist = songInput.value.trim();
    if (singers.length === 0) return;

    // Cancel any pending rotation search so a late response can't pop a stale
    // dropdown whose Link/DL&Link buttons would no-op (singers already cleared).
    hideRotSearchDropdown();
    rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');
    try {
        const response = await fetch('/rotation/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ singers: singers, song_artist: songArtist })
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
        singerPillInput.clear();
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
// Bumped whenever a pending search should be discarded (submit, hide, cancel).
// In-flight fetches capture this at start and must re-check before rendering,
// so stale responses can't resurrect the dropdown after a save/reset.
let rotSearchGen = 0;

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
    const myGen = rotSearchGen;
    try {
        const resp = await fetch('/rotation/search?q=' + encodeURIComponent(query));
        if (myGen !== rotSearchGen) return;  // superseded (e.g. user pressed Enter to save)
        if (!resp.ok) return;
        const data = await resp.json();
        if (myGen !== rotSearchGen) return;
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
        const singers = singerPillInput.getSingers();
        if (singers.length === 0) { if (singerInput) singerInput.focus(); return; }
    }

    hideRotSearchDropdown();
    rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');

    // Build (endpoint, body) for a search result given the identity fields
    // (either {id} for link mode, or {singers, song_artist} for add mode).
    const buildCall = (base) => {
        if (result.type === 'local') {
            return { endpoint: '/rotation/link', body: { ...base, file_path: result.path } };
        }
        if (result.type === 'divebar') {
            return { endpoint: '/rotation/download-and-link', body: {
                ...base, source: 'divebar', file_id: result.file_id, filename: result.filename,
            }};
        }
        if (result.type === 'youtube') {
            return { endpoint: '/rotation/download-and-link', body: {
                ...base, source: 'youtube', youtube_url: result.youtube_url, filename: result.filename,
            }};
        }
        if (result.type === 'make') {
            // Parse artist/title from query (try "Title - Artist" or "Artist - Title")
            const query = result.rawQuery || songInput.value.trim();
            const parts = query.split(/\s*-\s*/);
            const makeArtist = parts.length >= 2 ? parts[parts.length - 1] : '';
            const makeTitle = parts.length >= 2 ? parts.slice(0, -1).join(' - ') : query;
            return { endpoint: '/rotation/make', body: {
                ...base, song_artist: query, artist: makeArtist, title: makeTitle,
            }};
        }
        return null;
    };

    let ok = false;
    try {
        if (linkTargetId) {
            // Link mode: link result to existing rotation entry
            const entryId = parseInt(linkTargetId);
            // Also update the song_artist text to match the linked result
            const newSongArtist = result.song_artist || songInput.value.trim();
            if (newSongArtist) {
                await rotationMutate('/rotation/edit', { id: entryId, song_artist: newSongArtist });
            }
            const call = buildCall({ id: entryId });
            if (call) ok = await rotationMutate(call.endpoint, call.body);
        } else {
            // Add mode: create new rotation entry with linked result
            const singers = singerPillInput.getSingers();
            const base = {
                singers,
                song_artist: result.song_artist || songInput.value.trim(),
            };
            const call = buildCall(base);
            if (call) ok = await rotationMutate(call.endpoint, call.body);
        }
    } catch (e) {
        ok = false;
    }

    if (!ok) {
        // Server/network error: leave the form populated so the user can
        // retry without re-typing. apiCall() has already logged the server's
        // error message.
        showRotationIndicator('error');
        return;
    }

    // Success: clear form and/or exit link mode.
    hideRotSearchDropdown();
    if (linkTargetId) {
        exitLinkMode();
    } else {
        singerPillInput.clear();
        if (singerInput) singerInput.value = '';
        songInput.value = '';
        // Re-focus singer name for rapid-fire adds
        if (singerInput) singerInput.focus();
    }
    showRotationIndicator('success');
}

function hideRotSearchDropdown() {
    const dropdown = document.getElementById('rotation-search-dropdown');
    if (dropdown) dropdown.classList.add('hidden');
    rotSearchSelectedIdx = -1;
    // Cancel any pending/in-flight search so a late response can't re-show stale results
    rotSearchGen++;
    clearTimeout(rotSearchTimer);
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

// ============================================================================
// Public Singer Request Form — pending queue + settings modal
// ============================================================================

const SingRequests = (() => {
    let pollTimer = null;
    let pending = [];
    let config = { token: '', enabled: true, auto_approve: false, public_url: '', local_url: '', pending_count: 0 };

    async function fetchPending() {
        try {
            const resp = await fetch('/rotation/requests?status=pending');
            if (!resp.ok) return;
            const data = await resp.json();
            pending = data.requests || [];
            renderPanel();
            updateBadge(pending.length);
        } catch (e) { /* offline is fine */ }
    }

    async function fetchConfig() {
        try {
            const resp = await fetch('/rotation/requests/config');
            if (!resp.ok) return;
            config = await resp.json();
            applyConfigToModal();
            updateBadge(config.pending_count || 0);
        } catch (e) { /* offline is fine */ }
    }

    function updateBadge(count) {
        const badge = document.getElementById('pending-count-badge');
        if (!badge) return;
        if (count > 0) {
            badge.textContent = String(count);
            badge.classList.remove('hidden');
        } else {
            badge.classList.add('hidden');
        }
    }

    function renderPanel() {
        const panel = document.getElementById('pending-requests-panel');
        const list = document.getElementById('pending-requests-list');
        const count = document.getElementById('pending-requests-count');
        if (!panel || !list) return;
        if (!pending.length) { panel.classList.add('hidden'); return; }
        panel.classList.remove('hidden');
        if (count) count.textContent = String(pending.length);
        list.innerHTML = '';
        for (const req of pending) {
            list.appendChild(renderRow(req));
        }
    }

    function renderRow(req) {
        const row = document.createElement('div');
        row.className = 'pending-req-row';
        const song = [req.song_title, req.song_artist].filter(Boolean).join(' — ');
        const isKjPick = req.source_type === 'kj_pick';
        const isYoutube = req.source_type === 'youtube';
        let approveButton;
        if (isKjPick) {
            approveButton = `<span class="pr-approve-hint">pick a version below ↓</span>`;
        } else if (isYoutube) {
            // Two-button: KJ may approve-and-download the pasted URL, or
            // approve-without-download when the URL isn't a karaoke version
            // (they'll use the rotation 🔗 button to link a better file).
            approveButton = `
              <button class="btn-approve" data-id="${req.id}" title="Approve and download this YouTube URL as the karaoke video">Approve</button>
              <button class="btn-approve-skip" data-id="${req.id}" title="Approve without downloading — adds the singer to rotation; use the 🔗 button on the rotation row to link a better file">Approve, link later</button>
            `;
        } else {
            approveButton = `<button class="btn-approve" data-id="${req.id}">Approve</button>`;
        }
        row.innerHTML = `
          <div class="pr-main">
            <strong>${escapeHtml(req.singer_name)}</strong>
            <span class="pr-phone">${escapeHtml(req.phone || '')}</span>
            <span class="pr-song">${escapeHtml(song)}</span>
            <span class="pr-badge pr-${escapeHtml(req.source_type)}">${escapeHtml(req.source_type)}</span>
          </div>
          <div class="pr-actions">
            ${approveButton}
            <button class="btn-edit" data-id="${req.id}">Edit</button>
            <button class="btn-reject" data-id="${req.id}">Reject</button>
          </div>
        `;
        const approveBtn = row.querySelector('.btn-approve');
        if (approveBtn) approveBtn.addEventListener('click', () => approve(req.id));
        const approveSkipBtn = row.querySelector('.btn-approve-skip');
        if (approveSkipBtn) approveSkipBtn.addEventListener('click', () => approve(req.id, { skipDownload: true }));
        row.querySelector('.btn-edit').addEventListener('click', () => editInline(row, req));
        row.querySelector('.btn-reject').addEventListener('click', () => reject(req.id));
        if (req.source_type === 'youtube' && req.source_ref) {
            const preview = renderYouTubePreview(req.source_ref);
            if (preview) row.appendChild(preview);
        }
        if (Array.isArray(req.additional_singers) && req.additional_singers.length > 0) {
            const block = document.createElement('div');
            block.className = 'request-duet-partners';
            const title = document.createElement('div');
            title.className = 'request-duet-title';
            title.textContent = `👥 Singing with ${req.additional_singers.length} other${req.additional_singers.length === 1 ? '' : 's'}`;
            block.appendChild(title);
            const list = document.createElement('ul');
            list.className = 'request-duet-list';
            for (const p of req.additional_singers) {
                const li = document.createElement('li');
                const name = document.createElement('span');
                name.className = 'duet-name';
                name.textContent = p.name;
                li.appendChild(name);
                if (p.phone) {
                    const phone = document.createElement('a');
                    phone.className = 'duet-phone';
                    phone.href = `sms:${p.phone}`;
                    phone.textContent = p.phone;
                    li.appendChild(phone);
                } else {
                    const noPhone = document.createElement('span');
                    noPhone.className = 'duet-no-phone';
                    noPhone.textContent = '(no phone)';
                    li.appendChild(noPhone);
                }
                list.appendChild(li);
            }
            block.appendChild(list);
            row.appendChild(block);
        }
        if (isKjPick) {
            const picker = renderKjPickPicker(req);
            if (picker) row.appendChild(picker);
        }
        return row;
    }

    function renderYouTubePreview(url) {
        // Singers paste arbitrary URLs — only render an anchor if it's http(s).
        // Anything else (javascript:, data:, mailto:) gets shown as text only.
        let safeHref = null;
        try {
            const u = new URL(url);
            if (u.protocol === 'http:' || u.protocol === 'https:') safeHref = u.href;
        } catch (e) { /* not a valid URL — fall through to text-only */ }
        const ytId = extractYouTubeId(url);
        const wrap = document.createElement('div');
        wrap.className = 'pr-youtube-preview';
        const thumbHtml = ytId
            ? `<img class="pr-yt-thumb" src="https://i.ytimg.com/vi/${ytId}/default.jpg" alt="" loading="lazy" referrerpolicy="no-referrer">`
            : `<div class="pr-yt-thumb pr-yt-thumb-fallback" aria-hidden="true">▶</div>`;
        const linkHtml = safeHref
            ? `<a class="pr-yt-link" href="${escapeHtml(safeHref)}" target="_blank" rel="noopener noreferrer">▶ Watch on YouTube</a>`
            : `<span class="pr-yt-link pr-yt-link-unsafe">⚠ Unsafe URL — review manually</span>`;
        wrap.innerHTML = `
          ${thumbHtml}
          <div class="pr-yt-info">
            ${linkHtml}
            <div class="pr-yt-url" title="${escapeHtml(url)}">${escapeHtml(url)}</div>
          </div>
        `;
        return wrap;
    }

    function renderKjPickPicker(req) {
        // Parse the versions snapshot the singer submitted. Sort locals first,
        // then KN+divebar, then KN community, then KN YouTube-only so the KJ
        // can pick the best-quality option with a single glance.
        let versions;
        try {
            const meta = typeof req.source_meta === 'string'
                ? JSON.parse(req.source_meta || '{}')
                : (req.source_meta || {});
            versions = meta.versions || [];
        } catch (e) {
            return null;
        }
        if (!versions.length) {
            const empty = document.createElement('div');
            empty.className = 'pr-picker empty';
            empty.textContent = 'No versions in snapshot — edit or reject.';
            return empty;
        }
        const ranked = versions
            .map((v, idx) => ({ v, idx, bucket: versionBucket(v) }))
            .sort((a, b) => a.bucket - b.bucket);
        const container = document.createElement('div');
        container.className = 'pr-picker';
        for (const { v, idx } of ranked) {
            container.appendChild(renderVersionCard(req.id, v, idx));
        }
        return container;
    }

    function versionBucket(v) {
        if (v.source === 'local') return 0;
        if (v.source === 'kn' && v.kn && v.kn.divebar && v.kn.divebar.file_id) return 1;
        if (v.source === 'kn' && v.kn && v.kn.is_community) return 2;
        return 3;  // kn youtube-only
    }

    function renderVersionCard(reqId, v, idx) {
        const card = document.createElement('div');
        card.className = 'pr-version';
        let icon = '🎵';
        let primary = '';
        let secondary = '';
        if (v.source === 'local') {
            icon = '📁';
            primary = 'Local file';
            secondary = (v.local && (v.local.filename || v.local.path)) || '';
        } else if (v.source === 'kn' && v.kn) {
            const hasDivebar = v.kn.divebar && v.kn.divebar.file_id;
            const isCommunity = !!v.kn.is_community;
            icon = hasDivebar ? '💿' : (isCommunity ? '🎤' : '📺');
            const brand = v.kn.brand_code || v.kn.brand || 'Karaoke Nerds';
            if (hasDivebar) primary = `${brand} — Divebar mirror`;
            else if (isCommunity) primary = `${brand} — community`;
            else primary = `${brand} — YouTube`;
            secondary = v.kn.title || '';
        }
        card.innerHTML = `
          <div class="pr-v-icon">${icon}</div>
          <div class="pr-v-main">
            <div class="pr-v-primary">${escapeHtml(primary)}</div>
            <div class="pr-v-secondary">${escapeHtml(secondary)}</div>
          </div>
          <button class="btn-approve-version" data-idx="${idx}">Approve with this →</button>
        `;
        card.querySelector('.btn-approve-version').addEventListener('click', () => {
            approve(reqId, { versionIndex: idx });
        });
        return card;
    }

    async function approve(id, opts) {
        const { versionIndex, skipDownload } = opts || {};
        try {
            const body = {};
            if (versionIndex !== undefined) body.version_index = versionIndex;
            if (skipDownload) body.skip_download = true;
            const init = { method: 'POST' };
            if (Object.keys(body).length) {
                init.headers = { 'Content-Type': 'application/json' };
                init.body = JSON.stringify(body);
            }
            const resp = await fetch(`/rotation/requests/${id}/approve`, init);
            if (!resp.ok) {
                const data = await resp.json().catch(() => ({}));
                alert('Approval failed: ' + (data.error || resp.statusText));
                return;
            }
        } catch (e) { alert('Approval failed: ' + e.message); return; }
        await fetchPending();
        if (typeof fetchRotation === 'function') fetchRotation();
    }

    async function reject(id) {
        if (!confirm('Reject this request? The singer will be asked to see the KJ.')) return;
        try {
            const resp = await fetch(`/rotation/requests/${id}/reject`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            if (!resp.ok) {
                const data = await resp.json().catch(() => ({}));
                alert('Reject failed: ' + (data.error || resp.statusText));
                return;
            }
        } catch (e) { alert('Reject failed: ' + e.message); return; }
        await fetchPending();
    }

    function editInline(row, req) {
        const main = row.querySelector('.pr-main');
        main.innerHTML = `
          <input type="text" class="pr-edit-name" value="${escapeHtml(req.singer_name)}">
          <input type="text" class="pr-edit-artist" value="${escapeHtml(req.song_artist || '')}" placeholder="Artist">
          <input type="text" class="pr-edit-title" value="${escapeHtml(req.song_title || '')}" placeholder="Title">
        `;
        const actions = row.querySelector('.pr-actions');
        actions.innerHTML = `
          <button class="btn-save">Save & Approve</button>
          <button class="btn-cancel">Cancel</button>
        `;
        actions.querySelector('.btn-save').addEventListener('click', async () => {
            const payload = {
                singer_name: main.querySelector('.pr-edit-name').value.trim(),
                song_artist: main.querySelector('.pr-edit-artist').value.trim(),
                song_title: main.querySelector('.pr-edit-title').value.trim(),
            };
            const r1 = await fetch(`/rotation/requests/${req.id}/edit`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!r1.ok) { alert('Edit failed'); return; }
            await approve(req.id);
        });
        actions.querySelector('.btn-cancel').addEventListener('click', () => renderPanel());
    }

    function applyConfigToModal() {
        const eEl = document.getElementById('sing-enabled-toggle');
        const aEl = document.getElementById('sing-auto-approve-toggle');
        const mEl = document.getElementById('sing-accept-make-toggle');
        const pub = document.getElementById('sing-public-url');
        const loc = document.getElementById('sing-local-url');
        const qp = document.getElementById('sing-qr-public');
        const ql = document.getElementById('sing-qr-local');
        const tok = document.getElementById('sing-current-token');
        if (eEl) eEl.checked = !!config.enabled;
        if (aEl) aEl.checked = !!config.auto_approve;
        // Phase C — defaults to true since the backend defaults are "1"; if the
        // field is missing (older backend) we still show enabled.
        if (mEl) mEl.checked = config.accept_make_requests !== false;
        if (pub) pub.textContent = config.public_url || '—';
        if (loc) loc.textContent = config.local_url || '—';
        if (tok) tok.textContent = config.token || '—';
        const bust = Date.now();
        if (qp) qp.src = `/rotation/requests/qr.svg?scope=public&cb=${bust}`;
        if (ql) ql.src = `/rotation/requests/qr.svg?scope=local&cb=${bust}`;
    }

    async function postConfig(body) {
        const resp = await fetch('/rotation/requests/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) { alert('Config update failed'); return false; }
        return true;
    }

    async function toggleEnabled(checked) {
        // If the server update fails, fetchConfig() reverts the checkbox to the
        // actual server state so the UI can't lie.
        const ok = await postConfig({ enabled: checked });
        if (!ok) {
            const el = document.getElementById('sing-enabled-toggle');
            if (el) el.checked = !checked;
        }
        await fetchConfig();
    }

    async function toggleAutoApprove(checked) {
        const ok = await postConfig({ auto_approve: checked });
        if (!ok) {
            const el = document.getElementById('sing-auto-approve-toggle');
            if (el) el.checked = !checked;
        }
        await fetchConfig();
    }

    async function toggleAcceptMake(checked) {
        const ok = await postConfig({ accept_make_requests: checked });
        if (!ok) {
            const el = document.getElementById('sing-accept-make-toggle');
            if (el) el.checked = !checked;
        }
        await fetchConfig();
    }

    async function regenerate() {
        if (!confirm('Regenerate the event token? The current QR code stops working.')) return;
        if (await postConfig({ regenerate: true })) await fetchConfig();
    }

    async function setCustom() {
        const input = document.getElementById('sing-custom-token-input');
        if (!input) return;
        const raw = (input.value || '').trim();
        if (!/^\d{4}$/.test(raw)) {
            alert('Code must be exactly 4 digits.');
            input.focus();
            return;
        }
        if (raw === (config.token || '') ) {
            // Same as current — no point asking the singer to retype.
            input.value = '';
            return;
        }
        if (!confirm(`Set the event code to ${raw}? The current QR will keep working only if the new code matches its URL.`)) return;
        const resp = await fetch('/rotation/requests/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: raw }),
        });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            alert('Set code failed: ' + (data.error || resp.statusText));
            return;
        }
        input.value = '';
        await fetchConfig();
    }

    function copyUrl(scope) {
        const el = document.getElementById(scope === 'local' ? 'sing-local-url' : 'sing-public-url');
        if (!el) return;
        const url = el.textContent.trim();
        if (!url || url === '—') return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(url).catch(() => {});
        }
    }

    function openModal() {
        const m = document.getElementById('sing-requests-modal');
        if (!m) return;
        m.classList.remove('hidden');
        fetchConfig();
    }

    function closeModal() {
        const m = document.getElementById('sing-requests-modal');
        if (m) m.classList.add('hidden');
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
    }

    function start() {
        fetchPending();
        fetchConfig();
        pollTimer = setInterval(fetchPending, 5000);
    }

    return { start, openModal, closeModal, toggleEnabled, toggleAutoApprove, toggleAcceptMake, regenerate, setCustom, copyUrl };
})();

function openSingRequestsModal()   { SingRequests.openModal(); }
function closeSingRequestsModal()  { SingRequests.closeModal(); }
function toggleSingEnabled(c)      { SingRequests.toggleEnabled(c); }
function toggleSingAutoApprove(c)  { SingRequests.toggleAutoApprove(c); }
function toggleSingAcceptMake(c)   { SingRequests.toggleAcceptMake(c); }
function regenerateSingToken()     { SingRequests.regenerate(); }
function setCustomSingToken()      { SingRequests.setCustom(); }
function copySingUrl(scope)        { SingRequests.copyUrl(scope); }

window.addEventListener('DOMContentLoaded', () => SingRequests.start());
