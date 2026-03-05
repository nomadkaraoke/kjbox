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

// --- Download (async with status polling) ---

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

    // Kick off background download — completion detected via status polling
    const data = await apiCall('/download', { url });
    if (!data || !data.success) {
        downloadStageTimers.forEach(t => clearTimeout(t));
        downloadStageTimers = [];
        downloadBtn.disabled = false;
        downloadStatus.classList.add('hidden');
    }
    // If started OK, UI stays in "downloading" state until updateStatus detects completion
}

async function handleDownloadState(dl) {
    const downloadBtn = document.getElementById('download-btn');
    const downloadStatus = document.getElementById('download-status');
    const downloadStage = document.getElementById('download-stage');
    const urlInput = document.getElementById('youtube-url');

    if (!dl) return;

    if (dl.status === 'downloading') {
        // Show download progress if not already visible (e.g., after page refresh)
        if (downloadStatus.classList.contains('hidden')) {
            downloadBtn.disabled = true;
            downloadStatus.classList.remove('hidden');
            downloadStage.textContent = 'Downloading video...';
        }
    } else if (dl.status === 'completed') {
        downloadStageTimers.forEach(t => clearTimeout(t));
        downloadStageTimers = [];
        downloadBtn.disabled = false;
        downloadStatus.classList.add('hidden');
        log(`Downloaded "${dl.title}" successfully!`, 'success');
        urlInput.value = '';
        flashElement(urlInput, 'success');

        // Always refresh underlying media data so new track is available
        await refreshMediaData();

        // Clear any active search so user sees the folder view with new track
        if (searchActive) clearSearch();
        else renderFolderView(applyMediaFilter(localMediaItems));

        // Ensure downloads folder is expanded so new track is visible
        expandDownloadsFolder();

        fetch('/download/ack', { method: 'POST' });
    } else if (dl.status === 'error') {
        downloadStageTimers.forEach(t => clearTimeout(t));
        downloadStageTimers = [];
        downloadBtn.disabled = false;
        downloadStatus.classList.add('hidden');
        log(`Download failed: ${dl.error || 'Unknown error'}`, 'error');
        fetch('/download/ack', { method: 'POST' });
    }
    // 'idle' — nothing to do
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
let mp4OnlyFilter = localStorage.getItem('kj-mp4-only') === 'true';

function applyMediaFilter(items) {
    if (!mp4OnlyFilter) return items;
    return items.filter(item => {
        const ext = item.filename.split('.').pop().toLowerCase();
        return ext !== 'zip' && ext !== 'cdg';
    });
}

function toggleMp4Only() {
    mp4OnlyFilter = !mp4OnlyFilter;
    localStorage.setItem('kj-mp4-only', mp4OnlyFilter);
    document.getElementById('mp4-only-btn').classList.toggle('active', mp4OnlyFilter);
    if (searchActive) {
        const query = document.getElementById('catalog-search').value.trim();
        if (query) catalogSearch(query);
    } else {
        renderFolderView(applyMediaFilter(localMediaItems));
    }
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

            // Track background download progress
            if (data.download) await handleDownloadState(data.download);
        }
    } catch (error) {
        // Don't log periodic status check errors to avoid clutter
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
    populateAvResolutionSelect(data.video);
    populateAvHdmiPcmSelect(data.audio);
    populateAvVlcDeviceSelect(data.audio);
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
    const items = [
        { label: 'yt-dlp', ok: !!data.ytdlp_version },
        { label: 'EJS', ok: data.ejs_installed },
        { label: 'Deno', ok: data.deno_available },
        { label: 'Cookies', ok: data.cookies_present && data.cookies_valid },
    ];
    bar.innerHTML = items.map(({ label, ok }) => {
        const cls = ok ? 'av-health-ok' : (label === 'Cookies' ? 'av-health-warn' : 'av-health-error');
        const dotCls = ok ? 'av-dot-ok' : (label === 'Cookies' ? 'av-dot-warn' : 'av-dot-error');
        return `<span class="av-health-item ${cls}">${avDot(dotCls)} ${label}</span>`;
    }).join('');

    // Engine info
    const engineEl = document.getElementById('yt-engine-info');
    engineEl.innerHTML = `
        <div class="av-info-row">
            <span class="av-info-label">yt-dlp</span>
            ${avDot(data.ytdlp_version ? 'av-dot-ok' : 'av-dot-error')}
            <span class="av-info-value">${escapeHtml(data.ytdlp_version || 'not installed')}</span>
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
    } else if (!data.ejs_installed || !data.cookies_present || !data.cookies_valid) {
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
        row.appendChild(createCopyBtn(r.title));
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

    // Restore VNC size preference
    const savedVncSize = localStorage.getItem('kj-vnc-size');
    if (savedVncSize) setVncSize(savedVncSize);

    document.getElementById('mp4-only-btn').classList.toggle('active', mp4OnlyFilter);

    loadYTKaraokeToggle();
    updateStatus();
    updateMediaList();
    updateFillerMusicList();
    loadOverlays();
    checkCatalogAvailability();
    log('Nomad KJ Control initialized.');
});

setInterval(updateStatus, 2000);
