"""Overlay type implementations using pygame-ce.

Each overlay type manages its own pygame window and rendering logic.
Windows are borderless, always-on-top, and sized to their content.
"""

import io
import os
import time
from datetime import datetime

try:
    import pygame
except ImportError:
    pygame = None

try:
    import qrcode
except ImportError:
    qrcode = None

try:
    from PIL import Image, ImageDraw, ImageFont
    _pil_available = True
except ImportError:
    _pil_available = False

from overlay_config import (
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    calculate_position,
    hex_to_rgb,
)

# Font path for DejaVu Sans (Pi doesn't have Helvetica)
FONT_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
]

# Font family paths for Pillow rendering (DejaVu variants on Linux)
FONT_DIR = '/usr/share/fonts/truetype/dejavu'
FONT_FAMILIES = {
    'sans': {
        (False, False): 'DejaVuSans.ttf',
        (True, False): 'DejaVuSans-Bold.ttf',
        (False, True): 'DejaVuSans-Oblique.ttf',
        (True, True): 'DejaVuSans-BoldOblique.ttf',
    },
    'serif': {
        (False, False): 'DejaVuSerif.ttf',
        (True, False): 'DejaVuSerif-Bold.ttf',
        (False, True): 'DejaVuSerif-Italic.ttf',
        (True, True): 'DejaVuSerif-BoldItalic.ttf',
    },
    'mono': {
        (False, False): 'DejaVuSansMono.ttf',
        (True, False): 'DejaVuSansMono-Bold.ttf',
        (False, True): 'DejaVuSansMono-Oblique.ttf',
        (True, True): 'DejaVuSansMono-BoldOblique.ttf',
    },
}

EMOJI_FONT_PATHS = [
    '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf',
    '/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf',
]


def _get_font(size, bold=False):
    """Get a pygame font, preferring DejaVu Sans."""
    idx = 1 if bold else 0
    if idx < len(FONT_PATHS) and os.path.exists(FONT_PATHS[idx]):
        return pygame.font.Font(FONT_PATHS[idx], size)
    if os.path.exists(FONT_PATHS[0]):
        return pygame.font.Font(FONT_PATHS[0], size)
    return pygame.font.SysFont('DejaVu Sans', size, bold=bold)


def _get_pil_font(family, size, bold, italic):
    """Get a PIL ImageFont for the given family and style."""
    variants = FONT_FAMILIES.get(family, FONT_FAMILIES['sans'])
    filename = variants.get((bold, italic), variants.get((False, False)))
    path = os.path.join(FONT_DIR, filename)
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    # Try any variant of the family
    for fname in variants.values():
        p = os.path.join(FONT_DIR, fname)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    # Try original FONT_PATHS
    for fp in FONT_PATHS:
        if os.path.exists(fp):
            return ImageFont.truetype(fp, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _get_emoji_font(size):
    """Try to load an emoji font for fallback rendering."""
    for path in EMOJI_FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return None


def _is_emoji(char):
    """Check if a character is likely an emoji needing the emoji font."""
    cp = ord(char)
    return (
        0x1F600 <= cp <= 0x1F64F or  # Emoticons
        0x1F300 <= cp <= 0x1F5FF or  # Misc Symbols and Pictographs
        0x1F680 <= cp <= 0x1F6FF or  # Transport and Map
        0x1F1E0 <= cp <= 0x1F1FF or  # Flags
        0x2600 <= cp <= 0x26FF or    # Misc symbols
        0x2700 <= cp <= 0x27BF or    # Dingbats
        0x1F900 <= cp <= 0x1F9FF or  # Supplemental Symbols
        0x1FA00 <= cp <= 0x1FAFF or  # Symbols Extended-A
        cp == 0x200D or              # Zero Width Joiner
        cp == 0xFE0F or              # Variation Selector-16
        cp == 0x2764 or              # Heavy black heart
        cp == 0x2B50 or              # White medium star
        cp == 0x2B55                 # Heavy large circle
    )


def _wrap_text_pil(text, font, max_width):
    """Word-wrap text to fit within max_width pixels. Returns list of lines."""
    if max_width <= 0:
        return text.split('\n')

    result = []
    for paragraph in text.split('\n'):
        if not paragraph.strip():
            result.append('')
            continue
        words = paragraph.split(' ')
        current = ''
        for word in words:
            test = (current + ' ' + word).strip()
            if font.getlength(test) > max_width and current:
                result.append(current)
                current = word
            else:
                current = test
        if current:
            result.append(current)
    return result if result else ['']


def _draw_line_with_emoji(draw, x, y, line, font, emoji_font, color):
    """Draw a line of text, using emoji font for emoji characters."""
    runs = []
    current = ''
    is_emoji_run = False

    for char in line:
        char_is_emoji = _is_emoji(char)
        if char_is_emoji != is_emoji_run and current:
            runs.append((current, is_emoji_run))
            current = ''
        current += char
        is_emoji_run = char_is_emoji
    if current:
        runs.append((current, is_emoji_run))

    for text_run, is_emoji in runs:
        f = emoji_font if is_emoji else font
        try:
            draw.text((x, y), text_run, fill=color, font=f)
            x += f.getlength(text_run)
        except Exception:
            draw.text((x, y), text_run, fill=color, font=font)
            x += font.getlength(text_run)


def _render_text_pil(text, family, size, bold, italic, text_color_rgb,
                     max_width=0, text_align='left'):
    """Render multiline text to a pygame surface using Pillow.

    Returns (surface, width, height).
    """
    font = _get_pil_font(family, size, bold, italic)
    emoji_font = _get_emoji_font(size)

    lines = _wrap_text_pil(text, font, max_width)

    ascent, descent = font.getmetrics()
    line_height = ascent + descent + 4

    line_widths = []
    for line in lines:
        if line:
            bbox = font.getbbox(line)
            line_widths.append(bbox[2] - bbox[0])
        else:
            line_widths.append(0)

    text_width = max(line_widths) if line_widths else 1
    text_height = line_height * len(lines)
    canvas_width = max(text_width, max_width) if max_width > 0 else text_width
    canvas_width = max(int(canvas_width), 1)
    text_height = max(int(text_height), 1)

    img = Image.new('RGBA', (canvas_width, text_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    y = 0
    for i, line in enumerate(lines):
        if not line:
            y += line_height
            continue

        if text_align == 'center':
            x = (canvas_width - line_widths[i]) / 2
        elif text_align == 'right':
            x = canvas_width - line_widths[i]
        else:
            x = 0

        if emoji_font and any(_is_emoji(c) for c in line):
            _draw_line_with_emoji(draw, x, y, line, font, emoji_font, text_color_rgb)
        else:
            draw.text((x, y), line, fill=text_color_rgb, font=font)

        y += line_height

    raw = img.tobytes()
    surface = pygame.image.frombytes(raw, img.size, 'RGBA')
    return surface, canvas_width, text_height


def _make_bg_color(hex_color, opacity):
    """Create an (r, g, b) tuple with opacity pre-applied (blended with black)."""
    r, g, b = hex_to_rgb(hex_color)
    # Pre-multiply with opacity (since we can't do per-pixel alpha on the window)
    return (int(r * opacity), int(g * opacity), int(b * opacity))


class BaseOverlay:
    """Base class for all overlay types."""

    def __init__(self, overlay_id, config, show_over_video=False):
        self.overlay_id = overlay_id
        self.config = config
        self.show_over_video = show_over_video
        self.window = None
        self.surface = None
        self._visible = False
        self._width = 0
        self._height = 0
        self._x = 0
        self._y = 0

    def create_window(self):
        """Create the pygame window for this overlay. Call after calculating size."""
        if not pygame or self._width <= 0 or self._height <= 0:
            return

        self.window = pygame.Window(
            title=f'overlay-{self.overlay_id}',
            size=(self._width, self._height),
            position=(self._x, self._y),
            borderless=True,
            always_on_top=True,
        )
        self.surface = self.window.get_surface()
        self._visible = True

    def destroy_window(self):
        """Destroy the pygame window."""
        if self.window:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
            self.surface = None
            self._visible = False

    def show(self):
        """Show the overlay window."""
        if self.window and not self._visible:
            self.window.position = (self._x, self._y)
            self._visible = True
        elif not self.window:
            self.create_window()
            self.render()

    def hide(self):
        """Hide the overlay by moving it offscreen (pygame-ce doesn't have hide)."""
        if self.window and self._visible:
            self.destroy_window()
            self._visible = False

    @property
    def visible(self):
        return self._visible

    def update_config(self, config, show_over_video):
        """Update overlay config. Returns True if window needs recreation."""
        old_config = self.config
        self.config = config
        self.show_over_video = show_over_video
        return old_config != config

    def update(self):
        """Update overlay state (called each frame). Override in subclasses."""
        pass

    def render(self):
        """Render overlay content. Override in subclasses."""
        if self.window and self.surface:
            self.window.flip()

    def cleanup(self):
        """Clean up resources."""
        self.destroy_window()


class TickerOverlay(BaseOverlay):
    """Horizontal scrolling text ticker bar."""

    def __init__(self, overlay_id, config, show_over_video=False):
        super().__init__(overlay_id, config, show_over_video)
        self._scroll_x = SCREEN_WIDTH  # Start scrolling from right edge
        self._text_surface = None
        self._text_width = 0
        self._last_time = time.monotonic()
        self._setup()

    def _setup(self):
        font_size = self.config.get('font_size', 28)
        padding = self.config.get('padding', 10)
        position = self.config.get('position', 'bottom')

        font = _get_font(font_size)
        text = self.config.get('text', '')
        text_color = hex_to_rgb(self.config.get('text_color', '#FFFFFF'))
        self._text_surface = font.render(text, True, text_color)
        self._text_width = self._text_surface.get_width()

        self._height = font_size + padding * 2
        self._width = SCREEN_WIDTH  # Full-width bar

        x, y = calculate_position(position, self._width, self._height)
        self._x = x
        self._y = y

        self._bg_color = _make_bg_color(
            self.config.get('bg_color', '#1a1a2e'),
            self.config.get('bg_opacity', 0.85),
        )
        self._padding = padding

    def update_config(self, config, show_over_video):
        changed = super().update_config(config, show_over_video)
        if changed:
            self.destroy_window()
            self._scroll_x = SCREEN_WIDTH
            self._setup()
            self.create_window()
        return changed

    def update(self):
        now = time.monotonic()
        dt = now - self._last_time
        self._last_time = now

        speed = self.config.get('speed', 2)
        pixels_per_second = speed * 100  # speed=1 means 100px/s, speed=2 means 200px/s
        self._scroll_x -= pixels_per_second * dt

        # Reset when text has fully scrolled off the left edge
        if self._scroll_x < -self._text_width:
            self._scroll_x = SCREEN_WIDTH

    def render(self):
        if not self.window or not self.surface:
            return
        self.surface.fill(self._bg_color)
        self.surface.blit(self._text_surface, (int(self._scroll_x), self._padding))
        self.window.flip()


class StaticTextOverlay(BaseOverlay):
    """Static text block with background."""

    def __init__(self, overlay_id, config, show_over_video=False):
        super().__init__(overlay_id, config, show_over_video)
        self._text_surface = None
        self._setup()

    def _setup(self):
        font_size = self.config.get('font_size', 36)
        padding = self.config.get('padding', 12)
        bold = self.config.get('bold', True)
        italic = self.config.get('italic', False)
        family = self.config.get('font_family', 'sans')
        max_width = self.config.get('max_width', 0)
        text_align = self.config.get('text_align', 'left')
        text = self.config.get('text', '')
        text_color = hex_to_rgb(self.config.get('text_color', '#FFFFFF'))

        if _pil_available:
            self._text_surface, text_w, text_h = _render_text_pil(
                text, family, font_size, bold, italic, text_color,
                max_width=max_width, text_align=text_align,
            )
            self._width = text_w + padding * 2
            self._height = text_h + padding * 2
        else:
            font = _get_font(font_size, bold=bold)
            self._text_surface = font.render(text, True, text_color)
            self._width = self._text_surface.get_width() + padding * 2
            self._height = self._text_surface.get_height() + padding * 2

        position = self.config.get('position', 'top-right')
        custom_x = self.config.get('custom_x')
        custom_y = self.config.get('custom_y')
        self._x, self._y = calculate_position(position, self._width, self._height, custom_x, custom_y)

        self._bg_color = _make_bg_color(
            self.config.get('bg_color', '#000000'),
            self.config.get('bg_opacity', 0.7),
        )
        self._padding = padding

    def update_config(self, config, show_over_video):
        changed = super().update_config(config, show_over_video)
        if changed:
            self.destroy_window()
            self._setup()
            self.create_window()
        return changed

    def render(self):
        if not self.window or not self.surface:
            return
        self.surface.fill(self._bg_color)
        self.surface.blit(self._text_surface, (self._padding, self._padding))
        self.window.flip()


class ImageOverlay(BaseOverlay):
    """PNG image display at a configurable position."""

    def __init__(self, overlay_id, config, show_over_video=False):
        super().__init__(overlay_id, config, show_over_video)
        self._image_surface = None
        self._setup()

    def _setup(self):
        image_path = self.config.get('image_path', '')
        target_width = self.config.get('width', 150)

        if image_path and os.path.exists(image_path):
            img = pygame.image.load(image_path)
            # Scale maintaining aspect ratio
            orig_w, orig_h = img.get_size()
            if orig_w > 0:
                scale = target_width / orig_w
                target_height = int(orig_h * scale)
                self._image_surface = pygame.transform.smoothscale(img, (target_width, target_height))
                self._width = target_width
                self._height = target_height
            else:
                self._width = target_width
                self._height = target_width
                self._image_surface = None
        else:
            self._width = target_width
            self._height = target_width
            self._image_surface = None

        position = self.config.get('position', 'top-right')
        custom_x = self.config.get('custom_x')
        custom_y = self.config.get('custom_y')
        self._x, self._y = calculate_position(position, self._width, self._height, custom_x, custom_y)

    def update_config(self, config, show_over_video):
        changed = super().update_config(config, show_over_video)
        if changed:
            self.destroy_window()
            self._setup()
            self.create_window()
        return changed

    def render(self):
        if not self.window or not self.surface:
            return
        self.surface.fill((0, 0, 0))
        if self._image_surface:
            self.surface.blit(self._image_surface, (0, 0))
        self.window.flip()


class CountdownOverlay(BaseOverlay):
    """Live countdown timer to a target time."""

    def __init__(self, overlay_id, config, show_over_video=False):
        super().__init__(overlay_id, config, show_over_video)
        self._target_time = None
        self._font = None
        self._label_font = None
        self._last_text = None
        self._setup()

    def _setup(self):
        font_size = self.config.get('font_size', 40)
        padding = self.config.get('padding', 15)

        target_str = self.config.get('target_time', '')
        try:
            self._target_time = datetime.fromisoformat(target_str)
        except (ValueError, TypeError):
            self._target_time = None

        self._font = _get_font(font_size, bold=True)
        self._label_font = _get_font(max(14, font_size // 2))
        self._padding = padding

        # Calculate size from a sample string to get consistent window size
        label = self.config.get('label', '')
        expired_text = self.config.get('expired_text', 'TIME!')
        sample_time = '00:00:00'
        time_surf = self._font.render(sample_time, True, (255, 255, 255))
        label_surf = self._label_font.render(label, True, (255, 255, 255))
        expired_surf = self._font.render(expired_text, True, (255, 255, 255))

        content_w = max(time_surf.get_width(), label_surf.get_width(), expired_surf.get_width())
        content_h = label_surf.get_height() + time_surf.get_height() + 4

        self._width = content_w + padding * 2
        self._height = content_h + padding * 2

        position = self.config.get('position', 'top-center')
        custom_x = self.config.get('custom_x')
        custom_y = self.config.get('custom_y')
        self._x, self._y = calculate_position(position, self._width, self._height, custom_x, custom_y)

        self._text_color = hex_to_rgb(self.config.get('text_color', '#FF4444'))
        self._bg_color = _make_bg_color(
            self.config.get('bg_color', '#000000'),
            self.config.get('bg_opacity', 0.85),
        )

    def update_config(self, config, show_over_video):
        changed = super().update_config(config, show_over_video)
        if changed:
            self.destroy_window()
            self._setup()
            self.create_window()
        return changed

    def _get_remaining_text(self):
        """Get the countdown or expired text."""
        if not self._target_time:
            return self.config.get('expired_text', 'TIME!')

        now = datetime.now()
        remaining = self._target_time - now
        total_seconds = int(remaining.total_seconds())

        if total_seconds <= 0:
            return self.config.get('expired_text', 'TIME!')

        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        if hours > 0:
            return f'{hours}:{minutes:02d}:{seconds:02d}'
        return f'{minutes:02d}:{seconds:02d}'

    def update(self):
        pass  # Countdown calculates fresh each render

    def render(self):
        if not self.window or not self.surface:
            return

        remaining_text = self._get_remaining_text()
        label = self.config.get('label', '')

        self.surface.fill(self._bg_color)

        # Draw label
        label_surf = self._label_font.render(label, True, self._text_color)
        label_x = (self._width - label_surf.get_width()) // 2
        self.surface.blit(label_surf, (label_x, self._padding))

        # Draw time
        time_surf = self._font.render(remaining_text, True, self._text_color)
        time_x = (self._width - time_surf.get_width()) // 2
        time_y = self._padding + label_surf.get_height() + 4
        self.surface.blit(time_surf, (time_x, time_y))

        self.window.flip()


class QRCodeOverlay(BaseOverlay):
    """QR code generated from a URL, with optional label."""

    def __init__(self, overlay_id, config, show_over_video=False):
        super().__init__(overlay_id, config, show_over_video)
        self._qr_surface = None
        self._label_surface = None
        self._cached_url = None
        self._setup()

    def _setup(self):
        size = self.config.get('size', 180)
        padding = self.config.get('padding', 10)
        url = self.config.get('url', '')
        label = self.config.get('label', '')

        self._qr_surface = self._generate_qr(url, size)
        self._cached_url = url

        label_height = 0
        if label:
            font = _get_font(max(12, size // 10))
            self._label_surface = font.render(label, True, (255, 255, 255))
            label_height = self._label_surface.get_height() + 4
        else:
            self._label_surface = None

        self._width = size + padding * 2
        self._height = size + label_height + padding * 2
        self._padding = padding
        self._qr_size = size
        self._label_height = label_height

        position = self.config.get('position', 'bottom-right')
        custom_x = self.config.get('custom_x')
        custom_y = self.config.get('custom_y')
        self._x, self._y = calculate_position(position, self._width, self._height, custom_x, custom_y)

    def _generate_qr(self, url, size):
        """Generate a QR code as a pygame surface."""
        if not qrcode or not url:
            # Fallback: white square with "QR" text
            surf = pygame.Surface((size, size))
            surf.fill((255, 255, 255))
            font = _get_font(size // 4)
            text = font.render('QR', True, (0, 0, 0))
            surf.blit(text, ((size - text.get_width()) // 2, (size - text.get_height()) // 2))
            return surf

        qr = qrcode.QRCode(box_size=1, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')

        # Convert PIL image to pygame surface
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        qr_surf = pygame.image.load(buf, 'qr.png').convert_alpha()
        return pygame.transform.smoothscale(qr_surf, (size, size))

    def update_config(self, config, show_over_video):
        changed = super().update_config(config, show_over_video)
        if changed:
            self.destroy_window()
            self._setup()
            self.create_window()
        return changed

    def render(self):
        if not self.window or not self.surface:
            return
        self.surface.fill((0, 0, 0))

        # Draw QR code
        if self._qr_surface:
            self.surface.blit(self._qr_surface, (self._padding, self._padding))

        # Draw label below QR
        if self._label_surface:
            label_x = (self._width - self._label_surface.get_width()) // 2
            label_y = self._padding + self._qr_size + 4
            self.surface.blit(self._label_surface, (label_x, label_y))

        self.window.flip()


# Registry of overlay types to classes
OVERLAY_TYPE_MAP = {
    'ticker': TickerOverlay,
    'static_text': StaticTextOverlay,
    'image': ImageOverlay,
    'countdown': CountdownOverlay,
    'qr_code': QRCodeOverlay,
}


def create_overlay(overlay_data):
    """Factory function: create an overlay instance from config data."""
    overlay_type = overlay_data.get('type')
    cls = OVERLAY_TYPE_MAP.get(overlay_type)
    if not cls:
        return None
    return cls(
        overlay_id=overlay_data.get('id', ''),
        config=overlay_data.get('config', {}),
        show_over_video=overlay_data.get('show_over_video', False),
    )
