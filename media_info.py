"""
Cross-platform module to get currently playing media information.
Supports Windows (via winsdk), Linux (via MPRIS/D-Bus), and macOS (via osascript).
Includes album art color extraction for dynamic lighting.
"""
import sys
import asyncio
import threading
import time
import colorsys
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, List, Tuple
from io import BytesIO

logger = logging.getLogger(__name__)

# Try to import PIL for image processing
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("PIL not available - album cover color extraction disabled")


@dataclass
class MediaInfo:
    """Information about currently playing media."""
    title: str = ""
    artist: str = ""
    album: str = ""
    is_playing: bool = False
    source_app: str = ""  # e.g., "Spotify", "Chrome", "VLC"
    
    # Album art colors (list of RGB tuples, sorted by dominance)
    # Each color is (R, G, B) with values 0-255
    colors: List[Tuple[int, int, int]] = field(default_factory=list)
    
    # Thumbnail data (raw bytes, can be None)
    thumbnail_data: Optional[bytes] = None
    

def extract_colors_from_image(image_data: bytes, num_colors: int = 5) -> List[Tuple[int, int, int]]:
    """
    Extract dominant colors from image data.
    Uses a simple color quantization approach.
    
    Args:
        image_data: Raw image bytes (PNG, JPEG, etc.)
        num_colors: Number of colors to extract
    
    Returns:
        List of (R, G, B) tuples sorted by dominance
    """
    if not PIL_AVAILABLE:
        logger.debug("PIL not available, cannot extract colors")
        return []
    
    if not image_data:
        logger.debug("No image data provided for color extraction")
        return []
    
    logger.debug(f"Extracting colors from image data ({len(image_data)} bytes)")
    
    try:
        # Open image from bytes
        img = Image.open(BytesIO(image_data))
        
        # Convert to RGB if needed
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Resize for faster processing
        img = img.resize((100, 100), Image.Resampling.LANCZOS)
        
        # Get all pixels
        pixels = list(img.getdata())
        
        # Simple color quantization using binning
        # Group similar colors together
        color_counts: dict[Tuple[int, int, int], int] = {}
        
        for r, g, b in pixels:
            # Quantize to reduce color space (round to nearest 16)
            qr = (r // 24) * 24
            qg = (g // 24) * 24
            qb = (b // 24) * 24
            
            # Skip very dark or very light colors (less interesting for lighting)
            brightness = (r + g + b) / 3
            if brightness < 30 or brightness > 240:
                continue
            
            # Skip very desaturated colors (grays)
            max_c = max(r, g, b)
            min_c = min(r, g, b)
            if max_c - min_c < 30:
                continue
            
            key = (qr, qg, qb)
            color_counts[key] = color_counts.get(key, 0) + 1
        
        if not color_counts:
            # Fallback: just get most common colors without filtering
            color_counts = {}
            for r, g, b in pixels:
                qr = (r // 32) * 32
                qg = (g // 32) * 32
                qb = (b // 32) * 32
                key = (qr, qg, qb)
                color_counts[key] = color_counts.get(key, 0) + 1
        
        # Sort by count (most common first)
        sorted_colors = sorted(color_counts.items(), key=lambda x: -x[1])
        
        # Get top colors, ensuring they're distinct
        result = []
        for color, count in sorted_colors:
            # Check if this color is distinct enough from already selected colors
            is_distinct = True
            for existing in result:
                # Calculate color distance
                dr = abs(color[0] - existing[0])
                dg = abs(color[1] - existing[1])
                db = abs(color[2] - existing[2])
                if dr + dg + db < 60:  # Too similar
                    is_distinct = False
                    break
            
            if is_distinct:
                result.append(color)
                if len(result) >= num_colors:
                    break
        
        logger.debug(f"Extracted {len(result)} colors: {result}")
        return result
        
    except Exception as e:
        logger.warning(f"Failed to extract colors from image: {e}")
        return []


def rgb_to_hsv(r: int, g: int, b: int) -> Tuple[float, float, float]:
    """Convert RGB (0-255) to HSV (0-1)."""
    return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)


def hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """Convert HSV (0-1) to RGB (0-255)."""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


class MediaInfoProvider:
    """
    Cross-platform provider for currently playing media info.
    Automatically detects the OS and uses the appropriate backend.
    """
    
    def __init__(self):
        self._backend: Optional[_MediaInfoBackend] = None
        self._info = MediaInfo()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._callbacks: list[Callable[[MediaInfo], None]] = []
        self._poll_interval = 2.0  # Poll every 2 seconds
        
        # Cache for album art colors (to avoid re-processing)
        self._last_track_key = ""
        self._cached_colors: List[Tuple[int, int, int]] = []
        
        # Initialize platform-specific backend
        self._init_backend()
    
    def _init_backend(self) -> None:
        """Initialize the platform-specific backend."""
        logger.info(f"Initializing media backend for platform: {sys.platform}")
        if sys.platform == 'win32':
            try:
                self._backend = _WindowsMediaBackend()
                logger.info("Windows media backend initialized successfully")
            except ImportError as e:
                logger.error(f"Windows media backend not available: {e}")
                print(f"Windows media backend not available: {e}")
                print("Install with: pip install winrt-Windows.Media.Control")
                self._backend = _DummyBackend()
        elif sys.platform == 'linux':
            try:
                self._backend = _LinuxMediaBackend()
                logger.info("Linux MPRIS backend initialized successfully")
            except ImportError:
                logger.error("dbus-python not available")
                print("dbus-python not available. Install with: pip install dbus-python")
                self._backend = _DummyBackend()
        elif sys.platform == 'darwin':
            self._backend = _MacOSMediaBackend()
            logger.info("macOS media backend initialized")
        else:
            logger.warning(f"Unknown platform {sys.platform}, using dummy backend")
            self._backend = _DummyBackend()
    
    def add_callback(self, callback: Callable[[MediaInfo], None]) -> None:
        """Add a callback to be notified when media info changes."""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[MediaInfo], None]) -> None:
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def start(self) -> bool:
        """Start polling for media info."""
        if self._running:
            return True
        
        if not self._backend:
            return False
        
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        return True
    
    def stop(self) -> None:
        """Stop polling."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
    
    def get_info(self) -> MediaInfo:
        """Get current media info."""
        with self._lock:
            return MediaInfo(
                title=self._info.title,
                artist=self._info.artist,
                album=self._info.album,
                is_playing=self._info.is_playing,
                source_app=self._info.source_app,
                colors=list(self._info.colors),
                thumbnail_data=self._info.thumbnail_data
            )
    
    def _poll_loop(self) -> None:
        """Background polling loop."""
        poll_count = 0
        while self._running:
            poll_count += 1
            try:
                if self._backend:
                    new_info = self._backend.get_media_info()
                    
                    # Log media info periodically (every 10 polls = 20 seconds)
                    if poll_count % 10 == 1:
                        logger.info(f"Media poll: title='{new_info.title}', artist='{new_info.artist}', "
                                   f"playing={new_info.is_playing}, source='{new_info.source_app}', "
                                   f"thumbnail={len(new_info.thumbnail_data) if new_info.thumbnail_data else 0} bytes")
                    
                    # Extract colors from thumbnail if available and track changed
                    track_key = f"{new_info.artist}|{new_info.title}|{new_info.album}"
                    if track_key != self._last_track_key:
                        logger.info(f"Track changed: '{self._last_track_key}' -> '{track_key}'")
                        self._last_track_key = track_key
                        if new_info.thumbnail_data:
                            logger.info(f"Extracting colors from thumbnail ({len(new_info.thumbnail_data)} bytes)")
                            self._cached_colors = extract_colors_from_image(new_info.thumbnail_data)
                            logger.info(f"Extracted {len(self._cached_colors)} colors: {self._cached_colors}")
                        else:
                            logger.info("No thumbnail data available for color extraction")
                            self._cached_colors = []
                    
                    new_info.colors = self._cached_colors
                    
                    # Check if info changed
                    with self._lock:
                        changed = (
                            new_info.title != self._info.title or
                            new_info.artist != self._info.artist or
                            new_info.is_playing != self._info.is_playing or
                            new_info.colors != self._info.colors
                        )
                        self._info = new_info
                    
                    # Notify callbacks if changed
                    if changed:
                        for callback in self._callbacks:
                            try:
                                callback(new_info)
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"Error in media poll loop: {e}")
            
            time.sleep(self._poll_interval)


class _MediaInfoBackend:
    """Base class for platform-specific backends."""
    
    def get_media_info(self) -> MediaInfo:
        raise NotImplementedError


class _DummyBackend(_MediaInfoBackend):
    """Dummy backend when no platform support is available."""
    
    def get_media_info(self) -> MediaInfo:
        return MediaInfo()


class _WindowsMediaBackend(_MediaInfoBackend):
    """Windows backend using winrt (GlobalSystemMediaTransportControls)."""
    
    def __init__(self):
        # Import here to avoid errors on other platforms
        self._SessionManager = None
        self._PlaybackStatus = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_error: str = ""
        
        # Try the new winrt package structure first
        try:
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager,
                GlobalSystemMediaTransportControlsSessionPlaybackStatus,
            )
            self._SessionManager = GlobalSystemMediaTransportControlsSessionManager
            self._PlaybackStatus = GlobalSystemMediaTransportControlsSessionPlaybackStatus
        except ImportError:
            # Try alternative import for winsdk
            try:
                from winsdk.windows.media.control import (
                    GlobalSystemMediaTransportControlsSessionManager,
                    GlobalSystemMediaTransportControlsSessionPlaybackStatus,
                )
                self._SessionManager = GlobalSystemMediaTransportControlsSessionManager
                self._PlaybackStatus = GlobalSystemMediaTransportControlsSessionPlaybackStatus
            except ImportError as e:
                self._last_error = f"winrt import failed: {e}"
                raise ImportError(f"Neither winrt nor winsdk available: {e}")
    
    def _get_or_create_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create an event loop for this thread."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop
        except RuntimeError:
            # No event loop in this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop
    
    def get_media_info(self) -> MediaInfo:
        if not self._SessionManager:
            return MediaInfo()
        try:
            loop = self._get_or_create_loop()
            return loop.run_until_complete(self._get_media_info_async())
        except Exception as e:
            self._last_error = str(e)
            return MediaInfo()
    
    async def _get_media_info_async(self) -> MediaInfo:
        try:
            logger.debug("Requesting Windows media session manager...")
            manager = await self._SessionManager.request_async()
            if manager is None:
                self._last_error = "Failed to get session manager"
                logger.warning("Failed to get Windows session manager")
                return MediaInfo()
            
            session = manager.get_current_session()
            
            if session is None:
                # No active media session
                logger.debug("No active media session found")
                return MediaInfo()
            
            # Get source app first (for debugging)
            source_app = session.source_app_user_model_id or ""
            # Clean up app name (extract readable name)
            if source_app:
                if '!' in source_app:
                    source_app = source_app.split('!')[-1]
                source_app = source_app.split('\\')[-1].replace('.exe', '')
            
            # Get playback status
            playback_info = session.get_playback_info()
            is_playing = False
            if playback_info:
                is_playing = (
                    playback_info.playback_status == self._PlaybackStatus.PLAYING
                )
            
            # Get media properties
            properties = await session.try_get_media_properties_async()
            
            if properties is None:
                return MediaInfo(
                    is_playing=is_playing,
                    source_app=source_app
                )
            
            # Try to get thumbnail
            thumbnail_data = None
            try:
                thumbnail = properties.thumbnail
                logger.debug(f"Thumbnail property: {thumbnail}")
                if thumbnail:
                    # Read the thumbnail stream
                    logger.debug("Opening thumbnail stream...")
                    stream = await thumbnail.open_read_async()
                    if stream:
                        size = stream.size
                        logger.debug(f"Thumbnail stream size: {size} bytes")
                        if size > 0 and size < 10_000_000:  # Max 10MB
                            from winrt.windows.storage.streams import DataReader
                            reader = DataReader(stream)
                            await reader.load_async(size)
                            buffer = reader.read_buffer(size)
                            # Convert to bytes
                            thumbnail_data = bytes(buffer)
                            logger.info(f"Successfully read thumbnail: {len(thumbnail_data)} bytes")
                            reader.close()
                        else:
                            logger.warning(f"Thumbnail size invalid: {size}")
                        stream.close()
                    else:
                        logger.debug("Could not open thumbnail stream")
                else:
                    logger.debug("No thumbnail property available")
            except Exception as e:
                # Thumbnail extraction failed, continue without it
                self._last_error = f"Thumbnail error: {e}"
                logger.warning(f"Failed to extract thumbnail: {e}")
            
            return MediaInfo(
                title=properties.title or "",
                artist=properties.artist or "",
                album=properties.album_title or "",
                is_playing=is_playing,
                source_app=source_app,
                thumbnail_data=thumbnail_data
            )
        except Exception as e:
            self._last_error = str(e)
            return MediaInfo()


class _LinuxMediaBackend(_MediaInfoBackend):
    """Linux backend using D-Bus MPRIS2."""
    
    def __init__(self):
        import dbus
        self._dbus = dbus
        self._bus = dbus.SessionBus()
    
    def get_media_info(self) -> MediaInfo:
        try:
            # Find MPRIS players
            for service in self._bus.list_names():
                if service.startswith('org.mpris.MediaPlayer2.'):
                    try:
                        player = self._bus.get_object(service, '/org/mpris/MediaPlayer2')
                        properties = self._dbus.Interface(player, 'org.freedesktop.DBus.Properties')
                        
                        # Get playback status
                        status = properties.Get('org.mpris.MediaPlayer2.Player', 'PlaybackStatus')
                        is_playing = str(status) == 'Playing'
                        
                        # Get metadata
                        metadata = properties.Get('org.mpris.MediaPlayer2.Player', 'Metadata')
                        
                        title = str(metadata.get('xesam:title', '')) if metadata else ''
                        
                        artists = metadata.get('xesam:artist', []) if metadata else []
                        artist = str(artists[0]) if artists else ''
                        
                        album = str(metadata.get('xesam:album', '')) if metadata else ''
                        
                        # Try to get album art URL
                        thumbnail_data = None
                        art_url = str(metadata.get('mpris:artUrl', '')) if metadata else ''
                        if art_url:
                            thumbnail_data = self._fetch_art(art_url)
                        
                        # Extract app name from service
                        source_app = service.replace('org.mpris.MediaPlayer2.', '')
                        
                        if title or is_playing:
                            return MediaInfo(
                                title=title,
                                artist=artist,
                                album=album,
                                is_playing=is_playing,
                                source_app=source_app,
                                thumbnail_data=thumbnail_data
                            )
                    except Exception:
                        continue
            
            return MediaInfo()
        except Exception:
            return MediaInfo()
    
    def _fetch_art(self, url: str) -> Optional[bytes]:
        """Fetch album art from URL."""
        try:
            if url.startswith('file://'):
                # Local file
                path = url[7:]
                with open(path, 'rb') as f:
                    return f.read()
            elif url.startswith('http://') or url.startswith('https://'):
                # Remote URL
                import urllib.request
                with urllib.request.urlopen(url, timeout=2) as response:
                    return response.read()
        except Exception:
            pass
        return None


class _MacOSMediaBackend(_MediaInfoBackend):
    """macOS backend using osascript (AppleScript)."""
    
    def get_media_info(self) -> MediaInfo:
        import subprocess
        
        # Try common media apps
        apps = [
            ('Spotify', self._get_spotify_info),
            ('Music', self._get_music_app_info),
            ('iTunes', self._get_itunes_info),
        ]
        
        for app_name, getter in apps:
            try:
                info = getter()
                if info.title or info.is_playing:
                    info.source_app = app_name
                    return info
            except Exception:
                continue
        
        return MediaInfo()
    
    def _run_osascript(self, script: str) -> str:
        """Run AppleScript and return output."""
        import subprocess
        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            return result.stdout.strip()
        except Exception:
            return ""
    
    def _get_spotify_info(self) -> MediaInfo:
        """Get info from Spotify."""
        script = '''
        if application "Spotify" is running then
            tell application "Spotify"
                set trackName to name of current track
                set artistName to artist of current track
                set albumName to album of current track
                set artworkUrl to artwork url of current track
                set isPlaying to player state is playing
                return trackName & "|" & artistName & "|" & albumName & "|" & artworkUrl & "|" & isPlaying
            end tell
        end if
        '''
        result = self._run_osascript(script)
        if result and '|' in result:
            parts = result.split('|')
            if len(parts) >= 5:
                thumbnail_data = None
                if parts[3]:
                    thumbnail_data = self._fetch_art(parts[3])
                return MediaInfo(
                    title=parts[0],
                    artist=parts[1],
                    album=parts[2],
                    is_playing=parts[4].lower() == 'true',
                    thumbnail_data=thumbnail_data
                )
        return MediaInfo()
    
    def _fetch_art(self, url: str) -> Optional[bytes]:
        """Fetch album art from URL."""
        try:
            if url.startswith('http://') or url.startswith('https://'):
                import urllib.request
                with urllib.request.urlopen(url, timeout=2) as response:
                    return response.read()
        except Exception:
            pass
        return None
    
    def _get_music_app_info(self) -> MediaInfo:
        """Get info from Apple Music app."""
        script = '''
        if application "Music" is running then
            tell application "Music"
                if player state is playing then
                    set trackName to name of current track
                    set artistName to artist of current track
                    set albumName to album of current track
                    return trackName & "|" & artistName & "|" & albumName & "|true"
                end if
            end tell
        end if
        '''
        result = self._run_osascript(script)
        if result and '|' in result:
            parts = result.split('|')
            if len(parts) >= 4:
                return MediaInfo(
                    title=parts[0],
                    artist=parts[1],
                    album=parts[2],
                    is_playing=parts[3].lower() == 'true'
                )
        return MediaInfo()
    
    def _get_itunes_info(self) -> MediaInfo:
        """Get info from iTunes (older macOS)."""
        script = '''
        if application "iTunes" is running then
            tell application "iTunes"
                if player state is playing then
                    set trackName to name of current track
                    set artistName to artist of current track
                    set albumName to album of current track
                    return trackName & "|" & artistName & "|" & albumName & "|true"
                end if
            end tell
        end if
        '''
        result = self._run_osascript(script)
        if result and '|' in result:
            parts = result.split('|')
            if len(parts) >= 4:
                return MediaInfo(
                    title=parts[0],
                    artist=parts[1],
                    album=parts[2],
                    is_playing=parts[3].lower() == 'true'
                )
        return MediaInfo()


def format_colors_for_display(colors: List[Tuple[int, int, int]]) -> str:
    """Format color list for display/logging."""
    if not colors:
        return "(no colors)"
    parts = []
    for r, g, b in colors[:5]:
        parts.append(f"#{r:02x}{g:02x}{b:02x}")
    return " ".join(parts)


# Convenience function
def get_current_media() -> MediaInfo:
    """Get currently playing media info (one-shot, blocking)."""
    provider = MediaInfoProvider()
    if provider._backend:
        info = provider._backend.get_media_info()
        # Extract colors if thumbnail available
        if info.thumbnail_data:
            info.colors = extract_colors_from_image(info.thumbnail_data)
        return info
    return MediaInfo()


if __name__ == "__main__":
    # Test the module
    print("Testing media info provider...")
    print(f"Platform: {sys.platform}")
    print(f"PIL available: {PIL_AVAILABLE}")
    
    provider = MediaInfoProvider()
    print(f"Backend: {type(provider._backend).__name__}")
    
    info = get_current_media()
    print(f"\nCurrent media:")
    print(f"  Title: {info.title or '(none)'}")
    print(f"  Artist: {info.artist or '(none)'}")
    print(f"  Album: {info.album or '(none)'}")
    print(f"  Playing: {info.is_playing}")
    print(f"  Source: {info.source_app or '(none)'}")
    print(f"  Thumbnail: {'Yes' if info.thumbnail_data else 'No'} ({len(info.thumbnail_data) if info.thumbnail_data else 0} bytes)")
    print(f"  Colors: {format_colors_for_display(info.colors)}")
    
    # Show error if available (Windows)
    if hasattr(provider._backend, '_last_error') and provider._backend._last_error:
        print(f"  Last error: {provider._backend._last_error}")
    
    # Continuous test
    print("\nStarting continuous monitoring (Ctrl+C to stop)...")
    try:
        provider.start()
        while True:
            time.sleep(2)
            info = provider.get_info()
            if info.title:
                colors_str = format_colors_for_display(info.colors)
                print(f"  Now: {info.artist} - {info.title} [{info.source_app}] {'▶' if info.is_playing else '⏸'}")
                print(f"       Colors: {colors_str}")
            else:
                print(f"  No media detected (source: {info.source_app or 'none'})")
    except KeyboardInterrupt:
        print("\nStopped.")
        provider.stop()
