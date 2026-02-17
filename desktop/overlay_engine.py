#!/usr/bin/env python3
"""Overlay Engine — renders configurable overlays on the NomadPi display.

Reads overlay configuration from data/overlays.json (written by KJ Controller).
Creates pygame-ce windows for each enabled overlay, handles smooth scrolling
for ticker overlays, and auto-hides desktop-only overlays during video playback.

Usage:
    python3 overlay_engine.py [--config PATH]
"""

import argparse
import json
import os
import signal
import sys
import time

# Add desktop/ dir to path so overlay_config and overlay_types are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame

from overlay_config import apply_defaults, get_overlays_path
from overlay_types import create_overlay

FPS = 30
CONFIG_POLL_INTERVAL = 1.0  # seconds between checking overlays.json mtime


class OverlayEngine:
    """Main overlay engine: manages overlay lifecycle and render loop."""

    def __init__(self, config_path=None):
        self.config_path = config_path or get_overlays_path()
        self.overlays = {}  # id -> overlay instance
        self.karaoke_playing = False
        self._last_config_mtime = 0
        self._last_config_check = 0
        self._running = True

    def load_config(self):
        """Load overlays.json and return (karaoke_playing, overlay_list)."""
        try:
            with open(self.config_path, 'r') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f'overlay_engine: config load error: {e}', file=sys.stderr)
            return self.karaoke_playing, []

        karaoke_playing = data.get('karaoke_playing', False)
        overlay_list = data.get('overlays', [])

        # Apply defaults to each overlay
        for overlay in overlay_list:
            apply_defaults(overlay)

        return karaoke_playing, overlay_list

    def check_config(self):
        """Check if overlays.json has changed (mtime-based) and reload if so."""
        now = time.monotonic()
        if now - self._last_config_check < CONFIG_POLL_INTERVAL:
            return
        self._last_config_check = now

        try:
            mtime = os.path.getmtime(self.config_path)
        except OSError:
            return

        if mtime == self._last_config_mtime:
            return

        self._last_config_mtime = mtime
        self._reload_config()

    def _reload_config(self):
        """Reload config and sync overlay instances."""
        karaoke_playing, overlay_list = self.load_config()
        self.karaoke_playing = karaoke_playing

        # Build a map of new overlay configs by id
        new_configs = {}
        for overlay_data in overlay_list:
            oid = overlay_data.get('id')
            if oid:
                new_configs[oid] = overlay_data

        # Remove overlays that are no longer in config or are disabled
        for oid in list(self.overlays.keys()):
            if oid not in new_configs or not new_configs[oid].get('enabled', False):
                self.overlays[oid].cleanup()
                del self.overlays[oid]

        # Create or update overlays
        for oid, data in new_configs.items():
            if not data.get('enabled', False):
                continue

            if oid in self.overlays:
                # Update existing overlay config
                existing = self.overlays[oid]
                existing.update_config(
                    data.get('config', {}),
                    data.get('show_over_video', False),
                )
            else:
                # Create new overlay
                overlay = create_overlay(data)
                if overlay:
                    overlay.create_window()
                    overlay.render()
                    self.overlays[oid] = overlay

    def update_visibility(self):
        """Show/hide desktop-only overlays based on karaoke_playing state."""
        for overlay in self.overlays.values():
            if overlay.show_over_video:
                # Always visible — ensure shown
                if not overlay.visible:
                    overlay.show()
            else:
                # Desktop only — hide during video
                if self.karaoke_playing and overlay.visible:
                    overlay.hide()
                elif not self.karaoke_playing and not overlay.visible:
                    overlay.show()

    def run(self):
        """Main render loop."""
        pygame.init()
        clock = pygame.time.Clock()

        print(f'overlay_engine: started, config={self.config_path}', file=sys.stderr)

        # Initial config load
        self._reload_config()

        while self._running:
            # Check for config changes
            self.check_config()

            # Process pygame events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._running = False

            # Update visibility based on playback state
            self.update_visibility()

            # Update and render each overlay
            for overlay in list(self.overlays.values()):
                if overlay.visible:
                    overlay.update()
                    overlay.render()

            clock.tick(FPS)

        self.shutdown()

    def shutdown(self):
        """Clean up all overlays and quit pygame."""
        print('overlay_engine: shutting down...', file=sys.stderr)
        for overlay in self.overlays.values():
            overlay.cleanup()
        self.overlays.clear()
        pygame.quit()

    def handle_signal(self, signum, frame):
        """Handle SIGTERM/SIGINT for graceful shutdown."""
        print(f'overlay_engine: received signal {signum}', file=sys.stderr)
        self._running = False


def write_demo_config(path):
    """Write a demo overlays.json with one of each overlay type."""
    from datetime import datetime, timedelta
    target = (datetime.now() + timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%S')
    demo = {
        'karaoke_playing': False,
        'overlays': [
            {
                'id': 'demo-ticker',
                'type': 'ticker',
                'name': 'Demo Ticker',
                'enabled': True,
                'show_over_video': False,
                'config': {
                    'text': '    Welcome to Nomad Karaoke!  Sign up at the DJ booth!  Next singer: YOU!    ',
                    'speed': 1.5,
                    'position': 'bottom',
                    'font_size': 28,
                    'text_color': '#FFFFFF',
                    'bg_color': '#1a1a2e',
                    'bg_opacity': 0.9,
                    'padding': 10,
                },
            },
            {
                'id': 'demo-text',
                'type': 'static_text',
                'name': 'Demo Static Text',
                'enabled': True,
                'show_over_video': True,
                'config': {
                    'text': 'Happy Birthday Sarah!',
                    'position': 'top-right',
                    'font_size': 32,
                    'text_color': '#FFD700',
                    'bg_color': '#000000',
                    'bg_opacity': 0.75,
                    'padding': 14,
                },
            },
            {
                'id': 'demo-countdown',
                'type': 'countdown',
                'name': 'Demo Countdown',
                'enabled': True,
                'show_over_video': False,
                'config': {
                    'target_time': target,
                    'label': 'Last call in',
                    'expired_text': 'LAST CALL!',
                    'position': 'top-center',
                    'font_size': 36,
                    'text_color': '#FF4444',
                    'bg_color': '#000000',
                    'bg_opacity': 0.85,
                    'padding': 14,
                },
            },
            {
                'id': 'demo-qr',
                'type': 'qr_code',
                'name': 'Demo QR Code',
                'enabled': True,
                'show_over_video': False,
                'config': {
                    'url': 'https://nomadkaraoke.com',
                    'label': 'Scan to request a song',
                    'position': 'bottom-right',
                    'size': 160,
                    'padding': 10,
                },
            },
        ],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(demo, f, indent=2)
    return path


def main():
    parser = argparse.ArgumentParser(description='NomadPi Overlay Engine')
    parser.add_argument('--config', help='Path to overlays.json')
    parser.add_argument('--demo', action='store_true',
                        help='Run with demo overlays (ticker, text, countdown, QR code)')
    args = parser.parse_args()

    config_path = args.config
    if args.demo:
        import tempfile
        demo_dir = tempfile.mkdtemp(prefix='overlay-demo-')
        config_path = write_demo_config(os.path.join(demo_dir, 'overlays.json'))
        print(f'Demo mode: config at {config_path}', file=sys.stderr)
        print('Overlays: ticker (bottom), static text (top-right), countdown (top-center), QR (bottom-right)', file=sys.stderr)
        print('Edit the JSON file to test hot-reload. Ctrl+C to quit.', file=sys.stderr)

    engine = OverlayEngine(config_path=config_path)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, engine.handle_signal)
    signal.signal(signal.SIGINT, engine.handle_signal)

    engine.run()


if __name__ == '__main__':
    main()
