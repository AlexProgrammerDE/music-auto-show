"""
DMX Controller for ENTTEC Open DMX USB and compatible FTDI-based interfaces.
Cross-platform support using pyftdi.

DMX512 Protocol Notes:
- Baud rate: 250000
- Frame: 8 data bits, 2 stop bits, no parity (8N2)
- Break signal: LOW for 88-176 microseconds (minimum 88μs)
- Mark After Break (MAB): HIGH for 8-16 microseconds (minimum 8μs)
- Start code: First byte after break, typically 0x00 for dimmer data
- Up to 512 channels of data following the start code
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
    from pyftdi.ftdi import Ftdi
    from pyftdi.serialext import serial_for_url
    PYFTDI_AVAILABLE = True
except ImportError:
    PYFTDI_AVAILABLE = False

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


class FTDIDMXInterface(DMXInterface):
    """
    FTDI-based DMX interface using pyftdi library.
    
    This is the preferred interface for ENTTEC Open DMX USB on systems
    where pyftdi is available and libusb is properly configured.
    
    Note: On Windows, this requires the Zadig driver to replace the
    default FTDI driver with WinUSB/libusb.
    """
    
    def __init__(self, port: str = ""):
        self.port = port
        self._serial = None
        self._lock = threading.Lock()
        self._send_count = 0
        self._error_count = 0
    
    def open(self) -> bool:
        """Open the FTDI DMX interface."""
        if not PYFTDI_AVAILABLE:
            logger.warning("pyftdi not available - install with: pip install pyftdi")
            logger.warning("Note: pyftdi requires libusb. On Windows, use Zadig to install WinUSB driver.")
            return False
        
        try:
            # Auto-detect port if not specified
            if not self.port:
                self.port = self._detect_port()
            
            if not self.port:
                logger.warning("No FTDI device found for pyftdi")
                return False
            
            logger.info(f"Opening FTDI DMX interface: {self.port}")
            
            # Open serial connection with DMX settings
            # DMX uses 250000 baud, 8N2
            self._serial = serial_for_url(
                self.port,
                baudrate=250000,
                bytesize=8,
                parity='N',
                stopbits=2,
                timeout=1
            )
            
            logger.info(f"FTDI DMX interface opened successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to open FTDI interface: {e}")
            if "No backend" in str(e) or "libusb" in str(e).lower():
                logger.error("libusb not found. Install it:")
                logger.error("  Linux: sudo apt install libusb-1.0-0")
                logger.error("  Windows: Use Zadig to install WinUSB driver")
                logger.error("  macOS: brew install libusb")
            return False
    
    def _detect_port(self) -> str:
        """Auto-detect FTDI DMX device."""
        try:
            devices = Ftdi.list_devices()
            for device in devices:
                # ENTTEC Open DMX uses FT232R (VID:0403, PID:6001)
                vid, pid = device[0].vid, device[0].pid
                if vid == 0x0403:  # FTDI vendor ID
                    sn = device[0].sn
                    url = f"ftdi://ftdi:{pid:x}:{sn}/1"
                    logger.info(f"Found FTDI device: VID=0x{vid:04x} PID=0x{pid:04x} SN={sn}")
                    return url
        except Exception as e:
            logger.debug(f"FTDI device detection failed: {e}")
        return ""
    
    def close(self) -> None:
        """Close the interface."""
        with self._lock:
            if self._serial:
                try:
                    logger.debug(f"Closing FTDI interface. Sent {self._send_count} frames, {self._error_count} errors")
                    self._serial.close()
                except Exception as e:
                    logger.debug(f"Error closing FTDI interface: {e}")
                finally:
                    self._serial = None
    
    def send(self, data: bytes) -> bool:
        """Send DMX data with proper break signal."""
        with self._lock:
            if not self._serial:
                return False
            try:
                # Send break (low for >88us)
                self._serial.break_condition = True
                time.sleep(DMX_BREAK_SECONDS)
                self._serial.break_condition = False
                time.sleep(DMX_MAB_SECONDS)
                
                # Send start code (0) + DMX data
                self._serial.write(data)
                self._serial.flush()
                
                self._send_count += 1
                return True
            except Exception as e:
                self._error_count += 1
                if self._error_count <= 5:
                    logger.error(f"DMX send error: {e}")
                return False
    
    def is_open(self) -> bool:
        """Check if interface is open."""
        return self._serial is not None
    
    def get_stats(self) -> dict:
        """Get interface statistics."""
        return {
            "port": self.port,
            "send_count": self._send_count,
            "error_count": self._error_count,
            "is_open": self.is_open()
        }


class SerialDMXInterface(DMXInterface):
    """
    Generic serial DMX interface for ENTTEC Open DMX USB and compatible devices.
    
    This implementation uses multiple strategies to generate the DMX break signal:
    1. break_condition property (most reliable on Linux)
    2. send_break() with duration (platform-dependent behavior)
    3. Baudrate switching fallback (works on most platforms)
    
    The Enttec Open DMX USB uses an FTDI FT232R chip which requires proper
    break signal timing for DMX512 protocol compliance.
    """
    
    def __init__(self, port: str = ""):
        self.port = port
        self._serial = None
        self._lock = threading.Lock()
        self._break_method = None  # Will be detected on first send
        self._send_count = 0
        self._error_count = 0
        self._last_error = None
    
    def open(self) -> bool:
        """Open serial DMX interface."""
        if not PYSERIAL_AVAILABLE:
            logger.error("pyserial not available - install with: pip install pyserial")
            return False
        
        try:
            if not self.port:
                self.port = self._detect_port()
            
            if not self.port:
                # List available ports to help user
                ports = serial.tools.list_ports.comports()
                if ports:
                    logger.warning("No FTDI/DMX device auto-detected. Available ports:")
                    for p in ports:
                        vid_str = f"0x{p.vid:04x}" if p.vid else "N/A"
                        pid_str = f"0x{p.pid:04x}" if p.pid else "N/A"
                        logger.warning(f"  {p.device}: {p.description} (VID:{vid_str}, PID:{pid_str})")
                    logger.warning("Specify the port manually in your configuration if auto-detect fails.")
                else:
                    logger.error("No serial ports found. Check USB connection and drivers.")
                return False
            
            logger.info(f"Opening DMX interface on {self.port}")
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
            logger.info(f"  Serial port opened:")
            logger.info(f"    Baudrate: {self._serial.baudrate}")
            logger.info(f"    Bytesize: {self._serial.bytesize}")
            logger.info(f"    Parity: {self._serial.parity}")
            logger.info(f"    Stopbits: {self._serial.stopbits}")
            logger.info(f"    Port name: {self._serial.name}")
            logger.info(f"    Is open: {self._serial.is_open}")
            
            # Ensure RTS is set correctly for FTDI-based adapters
            # Some adapters use RTS to control the RS-485 driver direction
            try:
                self._serial.rts = True
                self._serial.dtr = True
                logger.info(f"    RTS: {self._serial.rts}, DTR: {self._serial.dtr}")
            except Exception as e:
                logger.debug(f"Could not set RTS/DTR (may not be supported): {e}")
            
            # Detect the best break method for this platform/device
            self._detect_break_method()
            
            logger.info(f"DMX interface opened successfully on {self.port}")
            logger.info(f"  Break method: {self._break_method}")
            logger.info(f"  Device: {self._get_device_info()}")
            
            return True
            
        except serial.SerialException as e:
            logger.error(f"Serial port error on {self.port}: {e}")
            if "Permission" in str(e) or "access" in str(e).lower():
                logger.error("Permission denied. On Linux, add user to 'dialout' group:")
                logger.error("  sudo usermod -a -G dialout $USER")
                logger.error("  (logout and login again for changes to take effect)")
            return False
        except Exception as e:
            logger.error(f"Failed to open serial interface on {self.port}: {e}")
            return False
    
    def _get_device_info(self) -> str:
        """Get device description for logging."""
        try:
            ports = serial.tools.list_ports.comports()
            for p in ports:
                if p.device == self.port:
                    return p.description or "Unknown device"
        except Exception:
            pass
        return "Unknown"
    
    def _detect_break_method(self) -> None:
        """Detect the best method for generating DMX break signals."""
        if not self._serial:
            return
        
        # Try break_condition property first (most precise on Linux)
        try:
            self._serial.break_condition = True
            time.sleep(0.0001)
            self._serial.break_condition = False
            self._break_method = "break_condition"
            logger.debug("Using break_condition property for DMX break")
            return
        except (AttributeError, serial.SerialException, IOError, OSError) as e:
            logger.debug(f"break_condition not supported: {e}")
        
        # Try send_break (platform-dependent timing)
        try:
            self._serial.send_break(duration=0.001)
            self._break_method = "send_break"
            logger.debug("Using send_break() for DMX break")
            return
        except (AttributeError, serial.SerialException, IOError, OSError) as e:
            logger.debug(f"send_break not supported: {e}")
        
        # Fallback to baudrate switching (works on most platforms)
        self._break_method = "baudrate_switch"
        logger.debug("Using baudrate switching for DMX break (fallback)")
    
    def _detect_port(self) -> str:
        """Auto-detect serial DMX device."""
        ports = serial.tools.list_ports.comports()
        
        # First pass: Look for known FTDI DMX devices
        for port in ports:
            desc = port.description.upper() if port.description else ""
            
            # ENTTEC Open DMX uses FTDI FT232R (VID:0403, PID:6001)
            if port.vid == 0x0403 and port.pid == 0x6001:
                logger.info(f"Found FTDI FT232R (likely Enttec Open DMX): {port.device}")
                return port.device
            
            # Other FTDI devices
            if port.vid == 0x0403:
                logger.info(f"Found FTDI device: {port.device} - {desc}")
                return port.device
            
            # Devices with DMX in name
            if 'DMX' in desc:
                logger.info(f"Found DMX device: {port.device} - {desc}")
                return port.device
            
            if 'FTDI' in desc or 'FT232' in desc:
                logger.info(f"Found FTDI device: {port.device} - {desc}")
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
                    # Send a final blackout before closing
                    logger.debug(f"Closing DMX interface. Sent {self._send_count} frames, {self._error_count} errors")
                    self._serial.close()
                except Exception as e:
                    logger.debug(f"Error closing serial port: {e}")
                finally:
                    self._serial = None
    
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
            # Note: send_break duration is platform-dependent
            # On Linux, tcsendbreak with non-zero duration may not work as expected
            # We use a longer duration to ensure the break is generated
            self._serial.send_break(duration=0.001)  # 1ms - longer than needed but more reliable
            time.sleep(DMX_MAB_SECONDS)
            return True
        except Exception as e:
            self._last_error = f"send_break failed: {e}"
            return False
    
    def _send_break_baudrate(self) -> bool:
        """
        Send break by temporarily switching to a lower baudrate.
        
        At 250000 baud, each bit is 4μs. To generate a ~176μs break,
        we send a 0x00 byte at a lower baudrate where the frame duration
        equals our desired break time.
        
        At 57600 baud, sending 0x00 takes about 173μs (10 bits * 17.36μs/bit)
        """
        try:
            # Flush any pending data first
            self._serial.flush()
            
            # Switch to lower baudrate for break signal
            self._serial.baudrate = 57600
            self._serial.write(b'\x00')
            self._serial.flush()
            
            # Switch back to DMX baudrate
            self._serial.baudrate = 250000
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
    
    def send(self, data: bytes) -> bool:
        """
        Send DMX data with proper break signal.
        
        DMX512 frame structure:
        1. Break: Line held LOW for 88-176μs
        2. Mark After Break (MAB): Line held HIGH for 8-16μs  
        3. Start code + data: 8N2 serial data at 250kbaud
        """
        with self._lock:
            if not self._serial:
                if self._send_count == 0:
                    logger.error("Send called but serial port is None!")
                return False
            
            try:
                # Log first frame details
                if self._send_count == 0:
                    non_zero = [(i, v) for i, v in enumerate(data) if v != 0]
                    logger.info(f"Sending first DMX frame: {len(data)} bytes, {len(non_zero)} non-zero channels")
                    if non_zero[:10]:
                        logger.info(f"  First non-zero values: {non_zero[:10]}")
                
                # Flush any pending output to ensure clean timing
                self._serial.reset_output_buffer()
                
                # Send break signal using detected method
                break_sent = False
                if self._break_method == "break_condition":
                    break_sent = self._send_break_condition()
                elif self._break_method == "send_break":
                    break_sent = self._send_break_function()
                else:
                    break_sent = self._send_break_baudrate()
                
                if not break_sent:
                    # Try fallback to baudrate switching
                    if self._break_method != "baudrate_switch":
                        logger.warning(f"Break method '{self._break_method}' failed, trying baudrate switch")
                        self._break_method = "baudrate_switch"
                        break_sent = self._send_break_baudrate()
                
                if not break_sent:
                    self._error_count += 1
                    if self._error_count <= 5:  # Only log first few errors
                        logger.error(f"Failed to send DMX break: {self._last_error}")
                    return False
                
                # Send DMX data (start code + channel data)
                bytes_written = self._serial.write(data)
                
                # Ensure data is fully transmitted before next frame
                self._serial.flush()
                
                self._send_count += 1
                
                # Log after first successful frame
                if self._send_count == 1:
                    logger.info(f"First DMX frame sent successfully ({bytes_written} bytes written)")
                
                return bytes_written == len(data)
                
            except serial.SerialTimeoutException:
                self._error_count += 1
                if self._error_count <= 5:
                    logger.error("DMX send timeout - device may be disconnected")
                return False
            except serial.SerialException as e:
                self._error_count += 1
                if self._error_count <= 5:
                    logger.error(f"DMX serial error: {e}")
                return False
            except Exception as e:
                self._error_count += 1
                if self._error_count <= 5:
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
            "last_error": self._last_error,
            "is_open": self.is_open()
        }


class SimulatedDMXInterface(DMXInterface):
    """Simulated DMX interface for testing without hardware."""
    
    def __init__(self):
        self._is_open = False
        self._last_data = bytes(513)
    
    def open(self) -> bool:
        self._is_open = True
        return True
    
    def close(self) -> None:
        self._is_open = False
    
    def send(self, data: bytes) -> bool:
        if self._is_open:
            self._last_data = data
            return True
        return False
    
    def is_open(self) -> bool:
        return self._is_open
    
    def get_last_data(self) -> bytes:
        """Get the last sent data (for visualization)."""
        return self._last_data


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
        
        logger.info(f"DMX output started at {self.fps} FPS ({1000/self.fps:.1f}ms per frame)")
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
                        logger.error("Multiple consecutive DMX send failures - check connection")
                    elif consecutive_errors >= 100 and consecutive_errors % 100 == 0:
                        logger.error(f"DMX send continues to fail ({consecutive_errors} consecutive errors)")
            
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
        if self._interface and hasattr(self._interface, 'get_stats'):
            stats["interface"] = self._interface.get_stats()
        
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
    logger.info("Creating DMX controller...")
    logger.info(f"  Simulation mode: {simulate}")
    logger.info(f"  Target FPS: {fps}")
    logger.info(f"  Port: {port or '(auto-detect)'}")
    logger.info(f"  pyserial available: {PYSERIAL_AVAILABLE}")
    logger.info(f"  pyftdi available: {PYFTDI_AVAILABLE}")
    
    if simulate:
        logger.info("Using simulated DMX interface (no hardware output)")
        interface = SimulatedDMXInterface()
    elif PYSERIAL_AVAILABLE:
        # Try pyserial first - works better on Windows with standard FTDI drivers
        # and on Linux without needing to configure libusb
        logger.info("Using pyserial-based DMX interface")
        interface = SerialDMXInterface(port)
    elif PYFTDI_AVAILABLE:
        # pyftdi requires libusb/Zadig driver on Windows
        logger.info("Using pyftdi-based DMX interface")
        interface = FTDIDMXInterface(port)
    else:
        logger.warning("No DMX library available!")
        logger.warning("Install pyserial with: pip install pyserial")
        logger.warning("Using simulation mode as fallback")
        interface = SimulatedDMXInterface()
    
    controller = DMXController(interface=interface, fps=fps)
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
