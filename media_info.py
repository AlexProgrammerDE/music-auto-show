"""
Cross-platform module to get currently playing media information.
Supports Windows (via winsdk), Linux (via MPRIS/D-Bus), and macOS (via osascript).
"""
import sys
import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Optional, Callable

@dataclass
class MediaInfo:
    """Information about currently playing media."""
    title: str = ""
    artist: str = ""
    album: str = ""
    is_playing: bool = False
    source_app: str = ""  # e.g., "Spotify", "Chrome", "VLC"
    

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
        
        # Initialize platform-specific backend
        self._init_backend()
    
    def _init_backend(self) -> None:
        """Initialize the platform-specific backend."""
        if sys.platform == 'win32':
            try:
                self._backend = _WindowsMediaBackend()
            except ImportError:
                print("winsdk not available. Install with: pip install winsdk")
                self._backend = _DummyBackend()
        elif sys.platform == 'linux':
            try:
                self._backend = _LinuxMediaBackend()
            except ImportError:
                print("dbus-python not available. Install with: pip install dbus-python")
                self._backend = _DummyBackend()
        elif sys.platform == 'darwin':
            self._backend = _MacOSMediaBackend()
        else:
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
                source_app=self._info.source_app
            )
    
    def _poll_loop(self) -> None:
        """Background polling loop."""
        while self._running:
            try:
                if self._backend:
                    new_info = self._backend.get_media_info()
                    
                    # Check if info changed
                    with self._lock:
                        changed = (
                            new_info.title != self._info.title or
                            new_info.artist != self._info.artist or
                            new_info.is_playing != self._info.is_playing
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
                pass  # Silently ignore errors
            
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
        # Using winrt-Windows.Media.Control package (pre-built wheels)
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus,
        )
        self._SessionManager = GlobalSystemMediaTransportControlsSessionManager
        self._PlaybackStatus = GlobalSystemMediaTransportControlsSessionPlaybackStatus
    
    def get_media_info(self) -> MediaInfo:
        try:
            # Run async code in sync context
            return asyncio.run(self._get_media_info_async())
        except Exception:
            return MediaInfo()
    
    async def _get_media_info_async(self) -> MediaInfo:
        try:
            manager = await self._SessionManager.request_async()
            session = manager.get_current_session()
            
            if session is None:
                return MediaInfo()
            
            # Get playback status
            playback_info = session.get_playback_info()
            is_playing = (
                playback_info.playback_status == self._PlaybackStatus.PLAYING
            )
            
            # Get media properties
            properties = await session.try_get_media_properties_async()
            
            # Get source app
            source_app = session.source_app_user_model_id or ""
            # Clean up app name (extract readable name)
            if source_app:
                # e.g., "Spotify.exe" -> "Spotify"
                source_app = source_app.split('!')[-1].split('\\')[-1].replace('.exe', '')
            
            return MediaInfo(
                title=properties.title or "",
                artist=properties.artist or "",
                album=properties.album_title or "",
                is_playing=is_playing,
                source_app=source_app
            )
        except Exception:
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
                        
                        # Extract app name from service
                        source_app = service.replace('org.mpris.MediaPlayer2.', '')
                        
                        if title or is_playing:  # Found active player
                            return MediaInfo(
                                title=title,
                                artist=artist,
                                album=album,
                                is_playing=is_playing,
                                source_app=source_app
                            )
                    except Exception:
                        continue
            
            return MediaInfo()
        except Exception:
            return MediaInfo()


class _MacOSMediaBackend(_MediaInfoBackend):
    """macOS backend using osascript (AppleScript)."""
    
    def get_media_info(self) -> MediaInfo:
        import subprocess
        
        # Try common media apps
        apps = [
            ('Spotify', self._get_spotify_info),
            ('Music', self._get_music_app_info),  # Apple Music
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
                set isPlaying to player state is playing
                return trackName & "|" & artistName & "|" & albumName & "|" & isPlaying
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


# Convenience function
def get_current_media() -> MediaInfo:
    """Get currently playing media info (one-shot, blocking)."""
    provider = MediaInfoProvider()
    if provider._backend:
        return provider._backend.get_media_info()
    return MediaInfo()


if __name__ == "__main__":
    # Test the module
    print("Testing media info provider...")
    print(f"Platform: {sys.platform}")
    
    info = get_current_media()
    print(f"\nCurrent media:")
    print(f"  Title: {info.title or '(none)'}")
    print(f"  Artist: {info.artist or '(none)'}")
    print(f"  Album: {info.album or '(none)'}")
    print(f"  Playing: {info.is_playing}")
    print(f"  Source: {info.source_app or '(none)'}")
