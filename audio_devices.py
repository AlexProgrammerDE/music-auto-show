"""
Cross-platform audio device enumeration.
Lists all usable audio input devices (loopbacks, microphones, line-ins, virtual devices).
Supports Windows (WASAPI), Linux (ALSA/PulseAudio), and macOS (CoreAudio).
"""
import logging
import platform
import shutil
import subprocess
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


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    """Return whether the already-normalized value contains any marker."""
    return any(needle in value for needle in needles)


def _canonical_device_name(name: str) -> str:
    """Normalize device names for rough default-output to loopback matching."""
    canonical = name.lower()
    for token in ("[loopback]", "(loopback)", "loopback", "default"):
        canonical = canonical.replace(token, " ")
    for char in "()[]{}:-_.,/\\|":
        canonical = canonical.replace(char, " ")
    return " ".join(canonical.split())


def _loopback_matches_output(loopback_name: str, output_name: str) -> bool:
    """Return whether a loopback device appears to mirror an output endpoint."""
    loopback_canonical = _canonical_device_name(loopback_name)
    output_canonical = _canonical_device_name(output_name)
    if not loopback_canonical or not output_canonical:
        return False
    return (
        output_canonical in loopback_canonical
        or loopback_canonical in output_canonical
    )


def _is_virtual_capture_name(name_lower: str) -> bool:
    """Return whether a name describes a virtual capture source."""
    return _contains_any(
        name_lower,
        (
            "blackhole",
            "soundflower",
            "vb-audio",
            "voicemeeter",
            "virtual cable",
            "cable output",
        ),
    )


def _is_line_input_name(name_lower: str) -> bool:
    """Return whether a name describes a line-level capture source."""
    return _contains_any(
        name_lower,
        ("line in", "line-in", "line input", "aux input"),
    )


def _is_microphone_name(name_lower: str) -> bool:
    """Return whether a name clearly describes a microphone capture source."""
    return _contains_any(
        name_lower,
        (
            "microphone",
            "mic ",
            " mic",
            "(mic",
            "mic)",
            "array",
            "webcam",
            "camera",
            "capture",
            "input",
        ),
    )


def _is_output_endpoint_name(name_lower: str) -> bool:
    """Return whether a name looks like a playback endpoint, not a microphone."""
    return _contains_any(
        name_lower,
        (
            "speaker",
            "speakers",
            "headphone",
            "headphones",
            "headset earphone",
            "hdmi",
            "displayport",
            "digital output",
            "line out",
            "line-out",
            "spdif",
            "s/pdif",
            "render",
            "output",
        ),
    )


def _is_transient_portaudio_client_name(name_lower: str) -> bool:
    """Return whether a JACK/PipeWire name belongs to this probing process."""
    return (
        "alsa capture [python" in name_lower
        or "pipewire alsa [python" in name_lower
    )


def _parse_pactl_nodes(output: str) -> list[dict[str, str]]:
    """Parse `pactl list sinks/sources` output into minimal node metadata."""
    nodes: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("Name:"):
            if current:
                nodes.append(current)
            current = {"name": line.removeprefix("Name:").strip()}
            continue

        if not current:
            continue

        if line.startswith("Description:"):
            current["description"] = line.removeprefix("Description:").strip()
        elif line.startswith("Sample Specification:"):
            current["sample_specification"] = line.removeprefix("Sample Specification:").strip()
        elif line.startswith("Monitor of Sink:"):
            current["monitor_of_sink"] = line.removeprefix("Monitor of Sink:").strip()

    if current:
        nodes.append(current)

    return nodes


def _read_pactl_nodes(kind: str) -> list[dict[str, str]]:
    """Read PipeWire/PulseAudio node metadata if pactl is available."""
    try:
        result = subprocess.run(
            ["pactl", "list", kind],
            capture_output=True,
            check=False,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if result.returncode != 0:
        return []

    return _parse_pactl_nodes(result.stdout)


def _read_pactl_default(kind: str) -> str:
    """Read the default PipeWire/PulseAudio sink or source name if available."""
    try:
        result = subprocess.run(
            ["pactl", f"get-default-{kind}"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    if result.returncode != 0:
        return ""

    return result.stdout.strip()


def _linux_audio_role_names() -> tuple[set[str], set[str]]:
    """Return canonical non-monitor source names and sink names from PipeWire/PulseAudio."""
    source_names: set[str] = set()
    sink_names: set[str] = set()

    for source in _read_pactl_nodes("sources"):
        name = source.get("name", "")
        description = source.get("description", "")
        if name.endswith(".monitor") or description.lower().startswith("monitor of "):
            continue
        if source.get("monitor_of_sink", "").lower() not in ("", "n/a"):
            continue

        for value in (name, description):
            canonical = _canonical_device_name(value)
            if canonical:
                source_names.add(canonical)

    for sink in _read_pactl_nodes("sinks"):
        for value in (sink.get("name", ""), sink.get("description", "")):
            canonical = _canonical_device_name(value)
            if canonical:
                sink_names.add(canonical)

    return source_names, sink_names


def _source_monitor_display_name(source: dict[str, str]) -> str:
    """Return a readable name for a PipeWire/PulseAudio monitor source."""
    description = source.get("description", "").strip()
    if description.lower().startswith("monitor of "):
        return description[len("Monitor of "):].strip()
    if description:
        return description

    name = source.get("name", "").strip()
    if name.endswith(".monitor"):
        name = name[:-len(".monitor")]
    return name


def _source_sample_rate(source: dict[str, str]) -> int:
    """Extract sample rate from a pactl sample specification."""
    sample_spec = source.get("sample_specification", "")
    for token in sample_spec.split():
        if token.endswith("Hz"):
            try:
                return int(token[:-2])
            except ValueError:
                return 44100
    return 44100


def _source_channel_count(source: dict[str, str]) -> int:
    """Extract channel count from a pactl sample specification."""
    sample_spec = source.get("sample_specification", "")
    for token in sample_spec.split():
        if token.endswith("ch"):
            try:
                return max(1, int(token[:-2]))
            except ValueError:
                return 2
    return 2


def _linux_monitor_devices(next_index: int) -> list[AudioDeviceInfo]:
    """Return PipeWire/PulseAudio monitor sources as explicit system-output devices."""
    if shutil.which("parec") is None:
        return []

    default_sink = _read_pactl_default("sink")
    devices: list[AudioDeviceInfo] = []

    for source in _read_pactl_nodes("sources"):
        source_name = source.get("name", "")
        description = source.get("description", "")
        monitor_of_sink = source.get("monitor_of_sink", "")
        is_monitor = (
            source_name.endswith(".monitor")
            or description.lower().startswith("monitor of ")
            or monitor_of_sink.lower() not in ("", "n/a")
        )
        if not is_monitor:
            continue

        display_name = _source_monitor_display_name(source)
        if not display_name:
            continue

        devices.append(
            AudioDeviceInfo(
                index=next_index,
                name=display_name,
                source_name=source_name,
                device_type=AudioDeviceType.MONITOR,
                channels=_source_channel_count(source),
                sample_rate=_source_sample_rate(source),
                host_api="PipeWire/PulseAudio",
                is_default_loopback=(
                    source_name == f"{default_sink}.monitor"
                    or monitor_of_sink == default_sink
                ),
            )
        )
        next_index += 1

    return devices


def _linux_device_role(
    name: str,
    source_names: set[str],
    sink_names: set[str],
) -> Optional[str]:
    """Resolve a Linux device name as source-only, sink-only, or both when known."""
    canonical = _canonical_device_name(name)
    if not canonical:
        return None

    is_source = any(
        canonical == source_name
        or canonical in source_name
        or source_name in canonical
        for source_name in source_names
    )
    is_sink = any(
        canonical == sink_name
        or canonical in sink_name
        or sink_name in canonical
        for sink_name in sink_names
    )

    if is_source and is_sink:
        return "both"
    if is_source:
        return "source"
    if is_sink:
        return "sink"
    return None


def _classify_device_type(
    name: str,
    host_api_name: str,
    platform_name: str,
    *,
    is_loopback_device: bool = False,
    max_output_channels: int = 0,
) -> AudioDeviceType:
    """
    Classify a device as loopback, microphone, line-in, etc.
    
    Args:
        name: Device name (lowercase)
        host_api_name: Name of the host API (e.g., "Windows WASAPI", "ALSA")
        platform_name: Current platform ("windows", "linux", "macos")
        is_loopback_device: PyAudioWPatch loopback marker, when available
        max_output_channels: Playback channels advertised by PortAudio
    
    Returns:
        AudioDeviceType classification
    """
    name_lower = name.lower()
    host_api_lower = host_api_name.lower()

    if is_loopback_device:
        return AudioDeviceType.LOOPBACK
    
    # Windows-specific detection
    if platform_name == "windows":
        # WASAPI loopback devices (PyAudioWPatch)
        if "loopback" in name_lower:
            return AudioDeviceType.LOOPBACK
        # Stereo Mix is Windows' built-in loopback
        if "stereo mix" in name_lower or "wave out mix" in name_lower or "what u hear" in name_lower:
            return AudioDeviceType.LOOPBACK
        # Virtual audio cables
        if _is_virtual_capture_name(name_lower):
            return AudioDeviceType.VIRTUAL
        # Line-in detection
        if _is_line_input_name(name_lower):
            return AudioDeviceType.LINE_IN
        if _is_microphone_name(name_lower):
            return AudioDeviceType.MICROPHONE
        if _is_output_endpoint_name(name_lower) or max_output_channels > 0:
            if "wasapi" in host_api_lower:
                return AudioDeviceType.LOOPBACK
            return AudioDeviceType.UNKNOWN
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
        if _is_line_input_name(name_lower):
            return AudioDeviceType.LINE_IN
        if _is_microphone_name(name_lower):
            return AudioDeviceType.MICROPHONE
        if _is_output_endpoint_name(name_lower):
            return AudioDeviceType.UNKNOWN
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
        if _is_line_input_name(name_lower):
            return AudioDeviceType.LINE_IN
        # Built-in microphone
        if "built-in" in name_lower or "macbook" in name_lower or "internal" in name_lower:
            return AudioDeviceType.MICROPHONE
        if _is_microphone_name(name_lower):
            return AudioDeviceType.MICROPHONE
        if _is_output_endpoint_name(name_lower):
            return AudioDeviceType.UNKNOWN
        # Default to microphone
        return AudioDeviceType.MICROPHONE
    
    return AudioDeviceType.UNKNOWN


def _is_openable_capture_device(
    p: Any,
    device_index: int,
    channels: int,
    sample_rate: int,
) -> bool:
    """Return whether the app can open this device with its capture format."""
    if channels <= 0:
        return False

    try:
        p.is_format_supported(
            sample_rate,
            input_device=device_index,
            input_channels=min(channels, 2),
            input_format=pyaudio.paFloat32,
        )
        return True
    except Exception as e:
        logger.debug(
            "Skipping audio device %s: float32 capture unsupported at %s Hz (%s)",
            device_index,
            sample_rate,
            e,
        )
        return False


def _should_hide_device(device_type: AudioDeviceType, name: str) -> bool:
    """Filter playback endpoints that PortAudio exposes as capture-shaped devices."""
    name_lower = name.lower()
    if _is_transient_portaudio_client_name(name_lower):
        return True

    if device_type != AudioDeviceType.UNKNOWN:
        return False

    if _is_output_endpoint_name(name_lower):
        return True

    return False


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
        List of AudioDeviceInfo objects sorted by likely user-facing relevance
    """
    devices: list[AudioDeviceInfo] = []
    
    if not PYAUDIO_AVAILABLE:
        logger.warning("PyAudio not available - cannot enumerate audio devices")
        return devices
    
    platform_name = _get_system_platform()
    linux_source_names: set[str] = set()
    linux_sink_names: set[str] = set()
    if platform_name == "linux":
        linux_source_names, linux_sink_names = _linux_audio_role_names()
    
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

        default_output_name = ""
        try:
            default_output_info = p.get_default_output_device_info()
            default_output_name = str(default_output_info.get("name") or "")
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

                max_output_raw = dev.get("maxOutputChannels", 0)
                max_output = int(max_output_raw) if max_output_raw is not None else 0
                
                # Get device name
                dev_name = str(dev.get("name", f"Device {i}"))
                
                # Get host API name
                host_api_idx_raw = dev.get("hostApi", 0)
                host_api_idx = int(host_api_idx_raw) if host_api_idx_raw is not None else 0
                host_api_name = host_apis.get(host_api_idx, "Unknown")

                is_loopback_device = bool(dev.get("isLoopbackDevice", False))
                
                # Classify device type
                device_type = _classify_device_type(
                    dev_name,
                    host_api_name,
                    platform_name,
                    is_loopback_device=is_loopback_device,
                    max_output_channels=max_output,
                )

                if platform_name == "linux":
                    linux_role = _linux_device_role(dev_name, linux_source_names, linux_sink_names)
                    if linux_role == "sink":
                        logger.debug("Skipping Linux sink exposed as capture device: %s", dev_name)
                        continue
                    if linux_role in ("source", "both") and device_type == AudioDeviceType.UNKNOWN:
                        device_type = AudioDeviceType.MICROPHONE
                
                # Get sample rate
                sample_rate_raw = dev.get("defaultSampleRate", 44100)
                sample_rate = int(sample_rate_raw) if sample_rate_raw is not None else 44100

                if _should_hide_device(device_type, dev_name):
                    logger.debug("Skipping playback endpoint exposed as capture device: %s", dev_name)
                    continue

                if not _is_openable_capture_device(p, i, max_input, sample_rate):
                    continue

                is_default_loopback = i == default_loopback_index
                if not is_default_loopback and is_loopback_device and default_output_name:
                    is_default_loopback = _loopback_matches_output(dev_name, default_output_name)
                
                # Create device info
                device_info = AudioDeviceInfo(
                    index=i,
                    name=dev_name,
                    device_type=device_type,
                    channels=max_input,
                    output_channels=max_output,
                    sample_rate=sample_rate,
                    host_api=host_api_name,
                    is_default=(i == default_input_index),
                    is_default_loopback=is_default_loopback,
                )
                
                devices.append(device_info)
                
            except Exception as e:
                logger.debug(f"Error getting device {i}: {e}")
                continue
        
        p.terminate()
        
    except Exception as e:
        logger.error(f"Error enumerating audio devices: {e}")
        return devices

    if platform_name == "linux":
        existing_names = {device.name for device in devices}
        for monitor_device in _linux_monitor_devices(
            max((device.index for device in devices), default=-1) + 1
        ):
            if monitor_device.name not in existing_names:
                devices.append(monitor_device)
                existing_names.add(monitor_device.name)
    
    # Sort devices: likely microphone sources first, then loopback and virtual devices.
    type_order = {
        AudioDeviceType.MICROPHONE: 0,
        AudioDeviceType.LINE_IN: 1,
        AudioDeviceType.LOOPBACK: 2,
        AudioDeviceType.MONITOR: 3,
        AudioDeviceType.VIRTUAL: 4,
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
        if device.name == name or device.source_name == name:
            return device
    
    # Try partial match as fallback (device names can change slightly)
    name_lower = name.lower()
    for device in devices:
        source_name_lower = device.source_name.lower()
        if (
            name_lower in device.name.lower()
            or device.name.lower() in name_lower
            or (source_name_lower and name_lower in source_name_lower)
            or (source_name_lower and source_name_lower in name_lower)
        ):
            logger.info(f"Partial match for '{name}': using '{device.name}'")
            return device
    
    return None


def get_default_loopback_device(*, allow_any: bool = True) -> Optional[AudioDeviceInfo]:
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

    if not allow_any:
        return None
    
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

    microphone_types = (
        AudioDeviceType.MICROPHONE,
        AudioDeviceType.LINE_IN,
    )
    
    # First try to find the default input that's a microphone
    for device in devices:
        if device.is_default and device.device_type in microphone_types:
            return device
    
    # Fall back to any physical microphone or line-in.
    for device in devices:
        if device.device_type in microphone_types:
            return device
    
    # Virtual capture sources are acceptable as a last resort, but output-shaped
    # endpoints must not be treated as microphones.
    for device in devices:
        if device.device_type == AudioDeviceType.VIRTUAL:
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
        # Prefer a reliable default loopback, then fall back to microphone.
        device = get_default_loopback_device(allow_any=False)
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
