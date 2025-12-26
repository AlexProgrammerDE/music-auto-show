"""
DMX Controller for ENTTEC Open DMX USB and compatible FTDI-based interfaces.
Cross-platform support using pyserial with proper break signal generation.

DMX512 Protocol Notes:
- Baud rate: 250000
- Frame: 8 data bits, 2 stop bits, no parity (8N2)
- Break signal: LOW for 88-176 microseconds (minimum 88μs)
- Mark After Break (MAB): HIGH for 8-16 microseconds (minimum 8μs)
- Start code: First byte after break, typically 0x00 for dimmer data
- Up to 512 channels of data following the start code

IMPORTANT: The Enttec Open DMX USB uses an FTDI FT232R chip which requires
special handling for the DMX break signal. On Windows, pyserial's break_condition
may not work reliably. This implementation uses multiple fallback methods:
1. break_condition property (works on Linux)
2. Baudrate switching (most reliable cross-platform method)
3. send_break() function (platform-dependent behavior)
"""
import logging
import threading
import time
import sys
from typing import Optional
from abc import ABC, abstractmethod

# Configure logging
logger = logging.getLogger(__name__)

try:
    import serial
    import serial.tools.list_ports
    PYSERIAL_AVAILABLE = True
except ImportError:
    PYSERIAL_AVAILABLE = False

# DMX512 timing constants (in seconds)
DMX_BREAK_SECONDS = 0.000176  # 176μs break (spec minimum is 88μs)
DMX_MAB_SECONDS = 0.000012    # 12μs Mark After Break (spec minimum is 8μs)


class DMXInterface(ABC):
    """Abstract base class for DMX interfaces."""
    
    @abstractmethod
    def open(self) -> bool:
        """Open the DMX interface."""
        pass
    
    @abstractmethod
    def close(self) -> None:
        """Close the DMX interface."""
        pass
    
    @abstractmethod
    def send(self, data: bytes) -> bool:
        """Send DMX data."""
        pass
    
    @abstractmethod
    def is_open(self) -> bool:
        """Check if interface is open."""
        pass
    
    def get_stats(self) -> dict:
        """Get interface statistics. Override in subclasses."""
        return {}


class SerialDMXInterface(DMXInterface):
    """
    Serial DMX interface for ENTTEC Open DMX USB and compatible devices.
    
    This implementation uses the baudrate-switching method as the PRIMARY
    method for generating DMX break signals, which is the most reliable
    cross-platform approach for FTDI-based adapters.
    
    The Enttec Open DMX USB uses an FTDI FT232R chip. Unlike the DMX USB Pro,
    it does not have a microcontroller and requires the host to generate
    proper DMX timing including the break signal.
    
    Break signal methods (in order of reliability):
    1. Baudrate switching (PRIMARY - most reliable on Windows/Linux/Mac)
    2. break_condition property (backup - works well on Linux)
    3. send_break() function (last resort - timing is platform-dependent)
    """
    
    def __init__(self, port: str = ""):
        self.port = port
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._break_method = "baudrate_switch"  # Default to most reliable method
        self._send_count = 0
        self._error_count = 0
        self._last_error = None
        self._consecutive_errors = 0
    
    def open(self) -> bool:
        """Open serial DMX interface."""
        if not PYSERIAL_AVAILABLE:
            logger.error("=" * 60)
            logger.error("pyserial not available!")
            logger.error("Install with: pip install pyserial")
            logger.error("=" * 60)
            return False
        
        try:
            if not self.port:
                self.port = self._detect_port()
            
            if not self.port:
                # List available ports to help user
                ports = list(serial.tools.list_ports.comports())
                logger.error("=" * 60)
                logger.error("NO DMX DEVICE DETECTED")
                logger.error("=" * 60)
                if ports:
                    logger.error("Available serial ports:")
                    for p in ports:
                        vid_str = f"0x{p.vid:04x}" if p.vid else "N/A"
                        pid_str = f"0x{p.pid:04x}" if p.pid else "N/A"
                        logger.error(f"  {p.device}: {p.description}")
                        logger.error(f"    VID: {vid_str}, PID: {pid_str}")
                    logger.error("")
                    logger.error("If your Enttec Open DMX USB is connected:")
                    logger.error("  - Check that the USB cable is properly connected")
                    logger.error("  - Try a different USB port")
                    logger.error("  - On Windows: Check Device Manager for COM port")
                    logger.error("  - Specify the port manually in configuration")
                else:
                    logger.error("No serial ports found!")
                    logger.error("  - Check USB connection")
                    logger.error("  - Install FTDI drivers if needed")
                    if sys.platform == 'win32':
                        logger.error("  - Windows: Download FTDI VCP drivers from ftdichip.com")
                    elif sys.platform == 'linux':
                        logger.error("  - Linux: sudo apt install libftdi1")
                logger.error("=" * 60)
                return False
            
            logger.info("=" * 60)
            logger.info("OPENING DMX INTERFACE")
            logger.info("=" * 60)
            logger.info(f"Port: {self.port}")
            logger.info(f"Device: {self._get_device_info()}")
            
            # Open with DMX settings: 250000 baud, 8N2
            self._serial = serial.Serial(
                self.port,
                baudrate=250000,
                bytesize=8,
                parity='N',
                stopbits=2,
                timeout=1,
                write_timeout=1
            )
            
            # Log serial port settings
            logger.info(f"Serial settings:")
            logger.info(f"  Baudrate: {self._serial.baudrate}")
            logger.info(f"  Bytesize: {self._serial.bytesize}, Stopbits: {self._serial.stopbits}")
            logger.info(f"  Parity: {self._serial.parity}")
            logger.info(f"  Port open: {self._serial.is_open}")
            
            # Set RTS/DTR - some adapters need this
            try:
                self._serial.rts = True
                self._serial.dtr = True
                logger.info(f"  RTS: {self._serial.rts}, DTR: {self._serial.dtr}")
            except Exception as e:
                logger.debug(f"Could not set RTS/DTR: {e}")
            
            # Detect the best break method for this platform/device
            self._detect_break_method()
            
            logger.info(f"Break method: {self._break_method}")
            logger.info("=" * 60)
            logger.info("DMX INTERFACE READY")
            logger.info("=" * 60)
            
            return True
            
        except serial.SerialException as e:
            logger.error("=" * 60)
            logger.error("SERIAL PORT ERROR")
            logger.error("=" * 60)
            logger.error(f"Port: {self.port}")
            logger.error(f"Error: {e}")
            if "Permission" in str(e) or "access" in str(e).lower():
                if sys.platform == 'linux':
                    logger.error("")
                    logger.error("Permission denied. Add user to 'dialout' group:")
                    logger.error("  sudo usermod -a -G dialout $USER")
                    logger.error("  (logout and login again for changes to take effect)")
                elif sys.platform == 'win32':
                    logger.error("")
                    logger.error("Access denied. The port may be in use by another program.")
                    logger.error("Close QLC+ or other DMX software and try again.")
            logger.error("=" * 60)
            return False
        except Exception as e:
            logger.error(f"Failed to open serial interface: {e}")
            return False
    
    def _get_device_info(self) -> str:
        """Get device description for logging."""
        try:
            ports = list(serial.tools.list_ports.comports())
            for p in ports:
                if p.device == self.port:
                    info = p.description or "Unknown"
                    if p.vid and p.pid:
                        info += f" (VID:0x{p.vid:04x} PID:0x{p.pid:04x})"
                    return info
        except Exception:
            pass
        return "Unknown"
    
    def _detect_break_method(self) -> None:
        """
        Detect the best method for generating DMX break signals.
        
        On Windows with FTDI devices, break_condition often doesn't work
        properly through the VCP driver. The baudrate switching method
        is more reliable across platforms.
        """
        if not self._serial:
            return
        
        # On Windows, prefer baudrate switching as it's more reliable with FTDI VCP
        if sys.platform == 'win32':
            self._break_method = "baudrate_switch"
            logger.info("Windows detected - using baudrate switching for DMX break")
            return
        
        # On Linux/Mac, try break_condition first as it's more precise
        try:
            self._serial.break_condition = True
            time.sleep(0.0001)
            self._serial.break_condition = False
            self._break_method = "break_condition"
            logger.info("Using break_condition property for DMX break")
            return
        except (AttributeError, serial.SerialException, IOError, OSError) as e:
            logger.debug(f"break_condition not supported: {e}")
        
        # Fallback to baudrate switching
        self._break_method = "baudrate_switch"
        logger.info("Using baudrate switching for DMX break (fallback)")
    
    def _detect_port(self) -> str:
        """Auto-detect serial DMX device."""
        ports = list(serial.tools.list_ports.comports())
        
        logger.info("Scanning for DMX devices...")
        
        # First pass: Look for known FTDI DMX devices
        for port in ports:
            desc = port.description.upper() if port.description else ""
            
            # ENTTEC Open DMX uses FTDI FT232R (VID:0403, PID:6001)
            if port.vid == 0x0403 and port.pid == 0x6001:
                logger.info(f"Found FTDI FT232R (Enttec Open DMX compatible): {port.device}")
                return port.device
            
            # Other FTDI devices that might be DMX adapters
            if port.vid == 0x0403:
                logger.info(f"Found FTDI device: {port.device} - {desc}")
                return port.device
            
            # Devices with DMX in name
            if 'DMX' in desc:
                logger.info(f"Found DMX device: {port.device} - {desc}")
                return port.device
        
        # Second pass: Any USB serial device
        for port in ports:
            device_upper = port.device.upper()
            if 'USB' in device_upper or 'ACM' in device_upper:
                logger.info(f"Using USB serial device: {port.device}")
                return port.device
        
        # Third pass: COM ports on Windows
        if sys.platform == 'win32':
            for port in ports:
                if 'COM' in port.device.upper():
                    logger.info(f"Using COM port: {port.device}")
                    return port.device
        
        return ""
    
    def close(self) -> None:
        """Close the interface."""
        with self._lock:
            if self._serial:
                try:
                    logger.info("=" * 60)
                    logger.info("CLOSING DMX INTERFACE")
                    logger.info("=" * 60)
                    logger.info(f"Frames sent: {self._send_count}")
                    logger.info(f"Errors: {self._error_count}")
                    if self._send_count > 0:
                        success_rate = ((self._send_count - self._error_count) / self._send_count) * 100
                        logger.info(f"Success rate: {success_rate:.1f}%")
                    self._serial.close()
                    logger.info("DMX interface closed")
                    logger.info("=" * 60)
                except Exception as e:
                    logger.debug(f"Error closing serial port: {e}")
                finally:
                    self._serial = None
    
    def _send_break_baudrate(self) -> bool:
        """
        Send break by temporarily switching to a lower baudrate.
        
        This is the most reliable method for FTDI-based adapters across
        all platforms. At a lower baudrate, sending a zero byte creates
        a longer "low" period that serves as the DMX break.
        
        At 76800 baud (non-standard but divisible from FTDI's clock):
        - Each bit is ~13μs
        - A zero byte (start bit + 8 zero bits + stop bits) = ~130μs LOW
        - This exceeds the 88μs minimum break requirement
        
        At 57600 baud:
        - Each bit is ~17.36μs
        - A zero byte = ~173μs LOW
        """
        try:
            # Flush any pending data first
            self._serial.reset_output_buffer()
            
            # Switch to lower baudrate for break signal
            # Using 76800 gives us ~130μs break which meets DMX spec
            self._serial.baudrate = 76800
            self._serial.write(b'\x00')
            self._serial.flush()
            
            # Switch back to DMX baudrate
            self._serial.baudrate = 250000
            
            # Small delay for Mark After Break
            time.sleep(DMX_MAB_SECONDS)
            return True
            
        except Exception as e:
            self._last_error = f"baudrate switch failed: {e}"
            # Try to restore baudrate
            try:
                self._serial.baudrate = 250000
            except Exception:
                pass
            return False
    
    def _send_break_condition(self) -> bool:
        """Send break using break_condition property."""
        try:
            self._serial.break_condition = True
            time.sleep(DMX_BREAK_SECONDS)
            self._serial.break_condition = False
            time.sleep(DMX_MAB_SECONDS)
            return True
        except Exception as e:
            self._last_error = f"break_condition failed: {e}"
            return False
    
    def _send_break_function(self) -> bool:
        """Send break using send_break() function."""
        try:
            # Note: send_break duration is platform-dependent and often ignored
            self._serial.send_break(duration=0.001)
            time.sleep(DMX_MAB_SECONDS)
            return True
        except Exception as e:
            self._last_error = f"send_break failed: {e}"
            return False
    
    def send(self, data: bytes) -> bool:
        """
        Send DMX data with proper break signal.
        
        DMX512 frame structure:
        1. Break: Line held LOW for 88-176μs
        2. Mark After Break (MAB): Line held HIGH for 8-16μs  
        3. Start code + data: 8N2 serial data at 250kbaud
        """
        with self._lock:
            if not self._serial or not self._serial.is_open:
                if self._send_count == 0:
                    logger.error("DMX send failed: serial port is not open!")
                return False
            
            try:
                # Log first frame details
                if self._send_count == 0:
                    non_zero = [(i, v) for i, v in enumerate(data) if v != 0]
                    logger.info("=" * 60)
                    logger.info("SENDING FIRST DMX FRAME")
                    logger.info("=" * 60)
                    logger.info(f"Frame size: {len(data)} bytes (start code + 512 channels)")
                    logger.info(f"Non-zero channels: {len(non_zero)}")
                    if non_zero[:10]:
                        logger.info(f"First values: {non_zero[:10]}")
                    logger.info(f"Break method: {self._break_method}")
                
                # Send break signal using detected method
                break_sent = False
                
                if self._break_method == "baudrate_switch":
                    break_sent = self._send_break_baudrate()
                elif self._break_method == "break_condition":
                    break_sent = self._send_break_condition()
                else:
                    break_sent = self._send_break_function()
                
                # If primary method fails, try baudrate switching as fallback
                if not break_sent and self._break_method != "baudrate_switch":
                    if self._consecutive_errors == 0:
                        logger.warning(f"Break method '{self._break_method}' failed, trying baudrate switch")
                    self._break_method = "baudrate_switch"
                    break_sent = self._send_break_baudrate()
                
                if not break_sent:
                    self._error_count += 1
                    self._consecutive_errors += 1
                    if self._consecutive_errors <= 3:
                        logger.error(f"Failed to send DMX break: {self._last_error}")
                    return False
                
                # Send DMX data (start code + channel data)
                bytes_written = self._serial.write(data)
                
                # Ensure data is fully transmitted before next frame
                self._serial.flush()
                
                self._send_count += 1
                self._consecutive_errors = 0
                
                # Log after first successful frame
                if self._send_count == 1:
                    logger.info(f"First frame sent successfully ({bytes_written} bytes)")
                    logger.info("=" * 60)
                    logger.info("DMX OUTPUT ACTIVE")
                    logger.info("=" * 60)
                
                return bytes_written == len(data)
                
            except serial.SerialTimeoutException:
                self._error_count += 1
                self._consecutive_errors += 1
                if self._consecutive_errors <= 3:
                    logger.error("DMX send timeout - device may be disconnected")
                return False
            except serial.SerialException as e:
                self._error_count += 1
                self._consecutive_errors += 1
                if self._consecutive_errors <= 3:
                    logger.error(f"DMX serial error: {e}")
                return False
            except Exception as e:
                self._error_count += 1
                self._consecutive_errors += 1
                if self._consecutive_errors <= 3:
                    logger.error(f"DMX send error: {e}")
                return False
    
    def is_open(self) -> bool:
        """Check if interface is open."""
        return self._serial is not None and self._serial.is_open
    
    def get_stats(self) -> dict:
        """Get interface statistics."""
        return {
            "port": self.port,
            "break_method": self._break_method,
            "send_count": self._send_count,
            "error_count": self._error_count,
            "consecutive_errors": self._consecutive_errors,
            "last_error": self._last_error,
            "is_open": self.is_open()
        }


class SimulatedDMXInterface(DMXInterface):
    """Simulated DMX interface for testing without hardware."""
    
    def __init__(self):
        self._is_open = False
        self._last_data = bytes(513)
        self._send_count = 0
    
    def open(self) -> bool:
        logger.info("=" * 60)
        logger.info("SIMULATED DMX INTERFACE")
        logger.info("=" * 60)
        logger.info("No actual DMX output - for testing only")
        logger.info("=" * 60)
        self._is_open = True
        return True
    
    def close(self) -> None:
        logger.info("Simulated DMX interface closed")
        self._is_open = False
    
    def send(self, data: bytes) -> bool:
        if self._is_open:
            self._last_data = data
            self._send_count += 1
            if self._send_count == 1:
                logger.info("Simulated DMX: First frame received")
            return True
        return False
    
    def is_open(self) -> bool:
        return self._is_open
    
    def get_last_data(self) -> bytes:
        """Get the last sent data (for visualization)."""
        return self._last_data
    
    def get_stats(self) -> dict:
        return {
            "type": "simulated",
            "send_count": self._send_count,
            "is_open": self._is_open
        }


class DMXController:
    """
    High-level DMX controller with continuous output.
    Manages universe data and sends at specified FPS.
    """
    
    def __init__(self, interface: Optional[DMXInterface] = None, universe_size: int = 512, fps: int = 40):
        self.universe_size = universe_size
        self.fps = fps
        self._data = bytearray(universe_size + 1)  # +1 for start code
        self._data[0] = 0  # Start code
        self._interface = interface
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._frame_count = 0
        self._start_time = 0.0
    
    def set_interface(self, interface: DMXInterface) -> None:
        """Set the DMX interface."""
        self._interface = interface
    
    def set_channel(self, channel: int, value: int) -> None:
        """Set a single channel value (1-512)."""
        if 1 <= channel <= self.universe_size:
            with self._lock:
                self._data[channel] = max(0, min(255, value))
    
    def set_channels(self, start_channel: int, values: list[int]) -> None:
        """Set multiple consecutive channels."""
        with self._lock:
            for i, value in enumerate(values):
                channel = start_channel + i
                if 1 <= channel <= self.universe_size:
                    self._data[channel] = max(0, min(255, value))
    
    def get_channel(self, channel: int) -> int:
        """Get a channel value."""
        if 1 <= channel <= self.universe_size:
            with self._lock:
                return self._data[channel]
        return 0
    
    def get_all_channels(self) -> list[int]:
        """Get all channel values."""
        with self._lock:
            return list(self._data[1:])
    
    def blackout(self) -> None:
        """Set all channels to 0."""
        with self._lock:
            for i in range(1, self.universe_size + 1):
                self._data[i] = 0
    
    def full_on(self) -> None:
        """Set all channels to 255."""
        with self._lock:
            for i in range(1, self.universe_size + 1):
                self._data[i] = 255
    
    def start(self) -> bool:
        """Start continuous DMX output."""
        if self._running:
            return True
        
        if not self._interface:
            logger.error("Cannot start DMX output: no interface configured")
            return False
        
        if not self._interface.is_open():
            logger.error("Cannot start DMX output: interface is not open")
            return False
        
        self._running = True
        self._frame_count = 0
        self._start_time = time.time()
        self._thread = threading.Thread(target=self._output_loop, daemon=True)
        self._thread.start()
        
        logger.info(f"DMX output thread started at {self.fps} FPS ({1000/self.fps:.1f}ms per frame)")
        return True
    
    def stop(self) -> None:
        """Stop continuous DMX output."""
        was_running = self._running
        self._running = False
        
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        
        if was_running:
            elapsed = time.time() - self._start_time
            if elapsed > 0:
                actual_fps = self._frame_count / elapsed
                logger.info(f"DMX output stopped. Sent {self._frame_count} frames in {elapsed:.1f}s ({actual_fps:.1f} FPS actual)")
    
    def _output_loop(self) -> None:
        """Continuous output loop."""
        interval = 1.0 / self.fps
        consecutive_errors = 0
        last_stats_time = time.time()
        
        while self._running:
            start = time.time()
            
            with self._lock:
                data = bytes(self._data)
            
            if self._interface:
                success = self._interface.send(data)
                if success:
                    self._frame_count += 1
                    consecutive_errors = 0
                else:
                    consecutive_errors += 1
                    if consecutive_errors == 10:
                        logger.error("=" * 60)
                        logger.error("DMX OUTPUT FAILING")
                        logger.error("=" * 60)
                        logger.error("Multiple consecutive send failures detected!")
                        logger.error("Check USB connection and try reconnecting the device.")
                        try:
                            stats = self._interface.get_stats()  # type: ignore
                            if stats.get('last_error'):
                                logger.error(f"Last error: {stats['last_error']}")
                        except AttributeError:
                            pass
                        logger.error("=" * 60)
                    elif consecutive_errors >= 100 and consecutive_errors % 100 == 0:
                        logger.error(f"DMX send continues to fail ({consecutive_errors} consecutive errors)")
            
            # Log stats every 30 seconds
            now = time.time()
            if now - last_stats_time >= 30.0 and self._frame_count > 0:
                elapsed = now - self._start_time
                actual_fps = self._frame_count / elapsed
                logger.debug(f"DMX stats: {self._frame_count} frames, {actual_fps:.1f} FPS, {consecutive_errors} recent errors")
                last_stats_time = now
            
            # Maintain consistent timing
            elapsed = time.time() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    def is_running(self) -> bool:
        """Check if output is running."""
        return self._running
    
    def get_stats(self) -> dict:
        """Get controller statistics."""
        elapsed = time.time() - self._start_time if self._start_time > 0 else 0
        actual_fps = self._frame_count / elapsed if elapsed > 0 else 0
        
        stats = {
            "running": self._running,
            "target_fps": self.fps,
            "actual_fps": actual_fps,
            "frame_count": self._frame_count,
            "elapsed_seconds": elapsed,
            "universe_size": self.universe_size
        }
        
        # Add interface stats if available
        if self._interface:
            try:
                stats["interface"] = self._interface.get_stats()  # type: ignore
            except AttributeError:
                pass
        
        return stats


def create_dmx_controller(port: str = "", simulate: bool = False, fps: int = 40) -> tuple[DMXController, DMXInterface]:
    """
    Factory function to create DMX controller with appropriate interface.
    
    Args:
        port: Serial port (auto-detect if empty)
        simulate: Use simulated interface (for testing)
        fps: DMX refresh rate
    
    Returns:
        Tuple of (DMXController, DMXInterface)
    """
    logger.info("=" * 60)
    logger.info("CREATING DMX CONTROLLER")
    logger.info("=" * 60)
    logger.info(f"Simulation mode: {simulate}")
    logger.info(f"Target FPS: {fps}")
    logger.info(f"Port: {port or '(auto-detect)'}")
    logger.info(f"Platform: {sys.platform}")
    logger.info(f"pyserial available: {PYSERIAL_AVAILABLE}")
    
    if simulate:
        interface = SimulatedDMXInterface()
    elif PYSERIAL_AVAILABLE:
        interface = SerialDMXInterface(port)
    else:
        logger.warning("=" * 60)
        logger.warning("NO DMX LIBRARY AVAILABLE")
        logger.warning("=" * 60)
        logger.warning("Install pyserial with: pip install pyserial")
        logger.warning("Using simulation mode as fallback")
        logger.warning("=" * 60)
        interface = SimulatedDMXInterface()
    
    controller = DMXController(interface=interface, fps=fps)
    logger.info("=" * 60)
    return controller, interface


def configure_logging(level: int = logging.INFO) -> None:
    """Configure logging for DMX controller module."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(level)
