"""
Cross-platform audio device enumeration.
Lists all usable audio input devices (loopbacks, microphones, line-ins, virtual devices).
Supports Windows (WASAPI), Linux (ALSA/PulseAudio), and macOS (CoreAudio).
"""
import logging
import platform
from typing import Optional, Any

from config import AudioDeviceInfo, AudioDeviceType

logger = logging.getLogger(__name__)

# Try to import PyAudio (with WASAPI support on Windows)
try:
    import pyaudiowpatch as pyaudio  # type: ignore
    PYAUDIO_AVAILABLE = True
    PYAUDIO_WPATCH = True
except ImportError:
    PYAUDIO_WPATCH = False
    try:
        import pyaudio  # type: ignore
        PYAUDIO_AVAILABLE = True
    except ImportError:
        PYAUDIO_AVAILABLE = False
        pyaudio = None  # type: ignore


def _get_system_platform() -> str:
    """Get the current platform: 'windows', 'linux', or 'macos'."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    return system


def _classify_device_type(name: str, host_api_name: str, platform_name: str) -> AudioDeviceType:
    """
    Classify a device as loopback, microphone, line-in, etc.
    
    Args:
        name: Device name (lowercase)
        host_api_name: Name of the host API (e.g., "Windows WASAPI", "ALSA")
        platform_name: Current platform ("windows", "linux", "macos")
    
    Returns:
        AudioDeviceType classification
    """
    name_lower = name.lower()
    
    # Windows-specific detection
    if platform_name == "windows":
        # WASAPI loopback devices (PyAudioWPatch)
        if "loopback" in name_lower:
            return AudioDeviceType.LOOPBACK
        # Stereo Mix is Windows' built-in loopback
        if "stereo mix" in name_lower or "wave out mix" in name_lower or "what u hear" in name_lower:
            return AudioDeviceType.LOOPBACK
        # Virtual audio cables
        if "cable output" in name_lower or "vb-audio" in name_lower or "voicemeeter" in name_lower:
            return AudioDeviceType.VIRTUAL
        # Line-in detection
        if "line in" in name_lower or "line-in" in name_lower or "aux" in name_lower:
            return AudioDeviceType.LINE_IN
        # Default to microphone for other input devices
        return AudioDeviceType.MICROPHONE
    
    # Linux-specific detection
    if platform_name == "linux":
        # PulseAudio monitor devices (loopback equivalent)
        if "monitor" in name_lower:
            return AudioDeviceType.MONITOR
        # ALSA loopback
        if "loopback" in name_lower:
            return AudioDeviceType.LOOPBACK
        # PipeWire/PulseAudio virtual devices
        if "pipewire" in name_lower or "pulse" in name_lower:
            # Check if it's a monitor
            if "monitor" in name_lower:
                return AudioDeviceType.MONITOR
            return AudioDeviceType.VIRTUAL
        # Line-in detection
        if "line" in name_lower or "aux" in name_lower:
            return AudioDeviceType.LINE_IN
        # Default to microphone
        return AudioDeviceType.MICROPHONE
    
    # macOS-specific detection
    if platform_name == "macos":
        # BlackHole, Soundflower, Loopback app
        if "blackhole" in name_lower or "soundflower" in name_lower or "loopback" in name_lower:
            return AudioDeviceType.LOOPBACK
        # Virtual cables
        if "virtual" in name_lower or "aggregate" in name_lower:
            return AudioDeviceType.VIRTUAL
        # Line-in
        if "line in" in name_lower or "line-in" in name_lower:
            return AudioDeviceType.LINE_IN
        # Built-in microphone
        if "built-in" in name_lower or "macbook" in name_lower or "internal" in name_lower:
            return AudioDeviceType.MICROPHONE
        # Default to microphone
        return AudioDeviceType.MICROPHONE
    
    return AudioDeviceType.UNKNOWN


def list_audio_devices() -> list[AudioDeviceInfo]:
    """
    List all available audio input devices.
    
    Returns a list of AudioDeviceInfo objects representing all devices that
    can be used for audio capture, including:
    - System audio loopbacks (WASAPI loopback on Windows, PulseAudio monitors on Linux)
    - Microphones
    - Line-in inputs
    - Virtual audio devices (VB-Cable, BlackHole, etc.)
    
    Returns:
        List of AudioDeviceInfo objects sorted by type (loopbacks first, then mics)
    """
    devices: list[AudioDeviceInfo] = []
    
    if not PYAUDIO_AVAILABLE:
        logger.warning("PyAudio not available - cannot enumerate audio devices")
        return devices
    
    platform_name = _get_system_platform()
    
    try:
        p = pyaudio.PyAudio()
        
        # Get host API info for classification
        host_apis: dict[int, str] = {}
        for i in range(p.get_host_api_count()):
            try:
                api_info = p.get_host_api_info_by_index(i)
                host_apis[i] = str(api_info.get("name", "Unknown"))
            except Exception:
                host_apis[i] = "Unknown"
        
        # Get default input device index
        default_input_index: Optional[int] = None
        try:
            default_info = p.get_default_input_device_info()
            idx = default_info.get("index")
            default_input_index = int(idx) if idx is not None else None
        except Exception:
            pass
        
        # Get default WASAPI loopback (Windows only, PyAudioWPatch)
        default_loopback_index: Optional[int] = None
        if PYAUDIO_WPATCH and hasattr(p, "get_default_wasapi_loopback"):
            try:
                loopback_info = p.get_default_wasapi_loopback()  # type: ignore
                idx = loopback_info.get("index")
                default_loopback_index = int(idx) if idx is not None else None
            except Exception:
                pass
        
        # Enumerate all devices
        for i in range(p.get_device_count()):
            try:
                dev = p.get_device_info_by_index(i)
                
                # Skip devices with no input channels
                max_input_raw = dev.get("maxInputChannels", 0)
                max_input = int(max_input_raw) if max_input_raw is not None else 0
                if max_input <= 0:
                    continue
                
                # Get device name
                dev_name = str(dev.get("name", f"Device {i}"))
                
                # Get host API name
                host_api_idx_raw = dev.get("hostApi", 0)
                host_api_idx = int(host_api_idx_raw) if host_api_idx_raw is not None else 0
                host_api_name = host_apis.get(host_api_idx, "Unknown")
                
                # Classify device type
                device_type = _classify_device_type(dev_name, host_api_name, platform_name)
                
                # Get sample rate
                sample_rate_raw = dev.get("defaultSampleRate", 44100)
                sample_rate = int(sample_rate_raw) if sample_rate_raw is not None else 44100
                
                # Create device info
                device_info = AudioDeviceInfo(
                    index=i,
                    name=dev_name,
                    device_type=device_type,
                    channels=max_input,
                    sample_rate=sample_rate,
                    host_api=host_api_name,
                    is_default=(i == default_input_index),
                    is_default_loopback=(i == default_loopback_index),
                )
                
                devices.append(device_info)
                
            except Exception as e:
                logger.debug(f"Error getting device {i}: {e}")
                continue
        
        p.terminate()
        
    except Exception as e:
        logger.error(f"Error enumerating audio devices: {e}")
        return devices
    
    # Sort devices: loopbacks/monitors first, then by type, then by name
    type_order = {
        AudioDeviceType.LOOPBACK: 0,
        AudioDeviceType.MONITOR: 1,
        AudioDeviceType.VIRTUAL: 2,
        AudioDeviceType.MICROPHONE: 3,
        AudioDeviceType.LINE_IN: 4,
        AudioDeviceType.UNKNOWN: 5,
    }
    
    devices.sort(key=lambda d: (
        type_order.get(d.device_type, 99),
        not d.is_default_loopback,  # Default loopback first
        not d.is_default,  # Then default input
        d.name.lower()
    ))
    
    return devices


def find_device_by_name(name: str) -> Optional[AudioDeviceInfo]:
    """
    Find a device by its name.
    
    Args:
        name: Device name to search for (exact match)
    
    Returns:
        AudioDeviceInfo if found, None otherwise
    """
    if not name:
        return None
    
    devices = list_audio_devices()
    for device in devices:
        if device.name == name:
            return device
    
    # Try partial match as fallback (device names can change slightly)
    name_lower = name.lower()
    for device in devices:
        if name_lower in device.name.lower() or device.name.lower() in name_lower:
            logger.info(f"Partial match for '{name}': using '{device.name}'")
            return device
    
    return None


def get_default_loopback_device() -> Optional[AudioDeviceInfo]:
    """
    Get the default system audio loopback device.
    
    Returns:
        AudioDeviceInfo for the default loopback, or None if not available
    """
    devices = list_audio_devices()
    
    # First try to find the default loopback
    for device in devices:
        if device.is_default_loopback:
            return device
    
    # Fall back to any loopback/monitor device
    for device in devices:
        if device.device_type in (AudioDeviceType.LOOPBACK, AudioDeviceType.MONITOR):
            return device
    
    return None


def get_default_microphone() -> Optional[AudioDeviceInfo]:
    """
    Get the default microphone device.
    
    Returns:
        AudioDeviceInfo for the default microphone, or None if not available
    """
    devices = list_audio_devices()
    
    # First try to find the default input that's a microphone
    for device in devices:
        if device.is_default and device.device_type == AudioDeviceType.MICROPHONE:
            return device
    
    # Fall back to any microphone
    for device in devices:
        if device.device_type == AudioDeviceType.MICROPHONE:
            return device
    
    # Fall back to any input device
    for device in devices:
        if device.device_type not in (AudioDeviceType.LOOPBACK, AudioDeviceType.MONITOR):
            return device
    
    return None


def get_best_device_for_mode(mode: str) -> Optional[AudioDeviceInfo]:
    """
    Get the best device for a given input mode.
    
    Args:
        mode: "loopback", "microphone", or "auto"
    
    Returns:
        AudioDeviceInfo for the best matching device
    """
    if mode == "loopback":
        return get_default_loopback_device()
    elif mode == "microphone":
        return get_default_microphone()
    else:  # auto
        # Prefer loopback, fall back to microphone
        device = get_default_loopback_device()
        if device:
            return device
        return get_default_microphone()


def print_device_list() -> None:
    """Print a formatted list of all audio devices (for CLI use)."""
    devices = list_audio_devices()
    
    if not devices:
        print("No audio input devices found.")
        return
    
    print("\nAvailable Audio Input Devices:")
    print("=" * 70)
    
    current_type: Optional[AudioDeviceType] = None
    for device in devices:
        # Print type header
        if device.device_type != current_type:
            current_type = device.device_type
            type_names = {
                AudioDeviceType.LOOPBACK: "System Audio Loopback",
                AudioDeviceType.MONITOR: "Monitor Devices (Linux)",
                AudioDeviceType.VIRTUAL: "Virtual Audio Devices",
                AudioDeviceType.MICROPHONE: "Microphones",
                AudioDeviceType.LINE_IN: "Line-In Inputs",
                AudioDeviceType.UNKNOWN: "Other Devices",
            }
            print(f"\n{type_names.get(current_type, 'Unknown')}:")
            print("-" * 40)
        
        # Print device info
        flags = []
        if device.is_default:
            flags.append("DEFAULT")
        if device.is_default_loopback:
            flags.append("DEFAULT LOOPBACK")
        
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        print(f"  [{device.index:3d}] {device.name}{flag_str}")
        print(f"        {device.channels}ch @ {device.sample_rate}Hz ({device.host_api})")
    
    print()
