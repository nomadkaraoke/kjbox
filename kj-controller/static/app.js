/* Nomad KJ Control — Application Logic */

// --- Logging ---

const logArea = document.getElementById('log-area');

function log(message, type = 'info') {
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    logArea.prepend(entry);
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

// --- Download ---

async function downloadSong() {
    const urlInput = document.getElementById('youtube-url');
    const downloadBtn = document.getElementById('download-btn');
    const downloadStatus = document.getElementById('download-status');
    const url = urlInput.value;
    if (!url) {
        log('Please enter a YouTube URL.', 'error');
        return;
    }
    log(`Downloading: ${url}`);
    downloadBtn.disabled = true;
    downloadStatus.classList.remove('hidden');
    const data = await apiCall('/download', { url });
    downloadBtn.disabled = false;
    downloadStatus.classList.add('hidden');
    if (data && data.success) {
        log(`Downloaded "${data.title}" successfully!`, 'success');
        urlInput.value = '';
        await updateMediaList();
    }
}

// --- Playback ---

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

// --- Volume & Seek ---

async function setVolume(target, level) {
    log(`Setting ${target} volume to ${level}`);
    await apiCall('/volume', { target, level: parseInt(level) });
}

async function setAudioDevice(device) {
    log(`Switching audio device to: ${device}`);
    await apiCall('/audio_device', { device });
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
    await apiCall('/filler_music', { track_name: trackName });
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

// --- Status ---

function formatTime(seconds) {
    if (isNaN(seconds) || seconds < 0) return "0:00";
    const min = Math.floor(seconds / 60);
    const sec = Math.floor(seconds % 60).toString().padStart(2, '0');
    return `${min}:${sec}`;
}

async function updateStatus() {
    try {
        const response = await fetch('/status');
        const data = await response.json();
        if (response.ok) {
            document.getElementById('player-state').textContent = data.state || 'unknown';
            document.getElementById('current-video').textContent = data.current_playing || 'None';
            document.getElementById('current-filler').textContent = data.current_filler_track || 'None';
            document.getElementById('current-time').textContent = formatTime(data.time);
            document.getElementById('total-time').textContent = formatTime(data.length);

            const audioWarning = document.getElementById('audio-warning');
            audioWarning.style.display = data.audio_error ? 'block' : 'none';

            if (data.audio_device) {
                const deviceSelect = document.getElementById('audio-device');
                if (deviceSelect.value !== data.audio_device) {
                    deviceSelect.value = data.audio_device;
                }
            }

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
        if (data.available) {
            searchInput.placeholder = `Search your library + ${data.total.toLocaleString()} catalog songs...`;
        } else {
            searchInput.placeholder = 'Search your library...';
        }
    } catch (error) {
        // Catalog not available - local search still works
    }
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

    updateStatus();
    updateMediaList();
    updateFillerMusicList();
    loadAudioDevices();
    checkCatalogAvailability();
    log('Nomad KJ Control initialized.');
});

setInterval(updateStatus, 2000);
