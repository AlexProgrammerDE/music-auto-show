"""
DMX Controller for ENTTEC Open DMX USB and compatible FTDI-based interfaces.
Cross-platform support using pyftdi.
"""
import threading
import time
from typing import Optional
from abc import ABC, abstractmethod

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
    """FTDI-based DMX interface (ENTTEC Open DMX USB)."""
    
    def __init__(self, port: str = ""):
        self.port = port
        self._serial = None
        self._lock = threading.Lock()
    
    def open(self) -> bool:
        """Open the FTDI DMX interface."""
        if not PYFTDI_AVAILABLE:
            print("pyftdi not available")
            return False
        
        try:
            # Auto-detect port if not specified
            if not self.port:
                self.port = self._detect_port()
            
            if not self.port:
                print("No FTDI device found")
                return False
            
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
            return True
        except Exception as e:
            print(f"Failed to open FTDI interface: {e}")
            return False
    
    def _detect_port(self) -> str:
        """Auto-detect FTDI DMX device."""
        try:
            devices = Ftdi.list_devices()
            for device in devices:
                # ENTTEC Open DMX uses FT232R (VID:0403, PID:6001)
                vid, pid = device[0].vid, device[0].pid
                if vid == 0x0403:  # FTDI vendor ID
                    serial = device[0].sn
                    return f"ftdi://ftdi:{pid:x}:{serial}/1"
        except Exception:
            pass
        return ""
    
    def close(self) -> None:
        """Close the interface."""
        with self._lock:
            if self._serial:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
    
    def send(self, data: bytes) -> bool:
        """Send DMX data with proper break signal."""
        with self._lock:
            if not self._serial:
                return False
            try:
                # Send break (low for >88us)
                self._serial.break_condition = True
                time.sleep(0.000092)  # 92us break
                self._serial.break_condition = False
                time.sleep(0.000012)  # 12us MAB (Mark After Break)
                
                # Send start code (0) + DMX data
                self._serial.write(data)
                return True
            except Exception as e:
                print(f"DMX send error: {e}")
                return False
    
    def is_open(self) -> bool:
        """Check if interface is open."""
        return self._serial is not None


class SerialDMXInterface(DMXInterface):
    """Generic serial DMX interface fallback."""
    
    def __init__(self, port: str = ""):
        self.port = port
        self._serial = None
        self._lock = threading.Lock()
    
    def open(self) -> bool:
        """Open serial DMX interface."""
        if not PYSERIAL_AVAILABLE:
            print("pyserial not available")
            return False
        
        try:
            if not self.port:
                self.port = self._detect_port()
            
            if not self.port:
                # List available ports to help user
                ports = serial.tools.list_ports.comports()
                if ports:
                    print("No FTDI/DMX device found. Available ports:")
                    for p in ports:
                        print(f"  {p.device}: {p.description} (VID:{p.vid}, PID:{p.pid})")
                else:
                    print("No serial ports found. Check USB connection.")
                return False
            
            print(f"Opening DMX on {self.port}")
            self._serial = serial.Serial(
                self.port,
                baudrate=250000,
                bytesize=8,
                parity='N',
                stopbits=2,
                timeout=1
            )
            return True
        except Exception as e:
            print(f"Failed to open serial interface: {e}")
            return False
    
    def _detect_port(self) -> str:
        """Auto-detect serial DMX device."""
        ports = serial.tools.list_ports.comports()
        for port in ports:
            desc = port.description.upper() if port.description else ""
            # Look for FTDI devices (ENTTEC Open DMX uses FTDI FT232R)
            if 'FTDI' in desc or 'FT232' in desc:
                return port.device
            # Or devices with DMX in name
            if 'DMX' in desc:
                return port.device
            # Check VID:PID for FTDI (0403:6001 is FT232R used by ENTTEC)
            if port.vid == 0x0403:
                return port.device
        # Return first USB serial if nothing specific found
        for port in ports:
            if 'USB' in port.device.upper() or 'COM' in port.device.upper():
                return port.device
        return ""
    
    def close(self) -> None:
        """Close the interface."""
        with self._lock:
            if self._serial:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
    
    def send(self, data: bytes) -> bool:
        """Send DMX data."""
        with self._lock:
            if not self._serial:
                return False
            try:
                self._serial.send_break(duration=0.000092)
                time.sleep(0.000012)
                self._serial.write(data)
                return True
            except Exception as e:
                print(f"DMX send error: {e}")
                return False
    
    def is_open(self) -> bool:
        """Check if interface is open."""
        return self._serial is not None


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
        
        if not self._interface or not self._interface.is_open():
            return False
        
        self._running = True
        self._thread = threading.Thread(target=self._output_loop, daemon=True)
        self._thread.start()
        return True
    
    def stop(self) -> None:
        """Stop continuous DMX output."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
    
    def _output_loop(self) -> None:
        """Continuous output loop."""
        interval = 1.0 / self.fps
        while self._running:
            start = time.time()
            
            with self._lock:
                data = bytes(self._data)
            
            if self._interface:
                self._interface.send(data)
            
            # Maintain consistent timing
            elapsed = time.time() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    def is_running(self) -> bool:
        """Check if output is running."""
        return self._running


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
    if simulate:
        interface = SimulatedDMXInterface()
    elif PYSERIAL_AVAILABLE:
        # Try pyserial first - works better on Windows with standard FTDI drivers
        interface = SerialDMXInterface(port)
    elif PYFTDI_AVAILABLE:
        # pyftdi requires libusb/Zadig driver on Windows
        interface = FTDIDMXInterface(port)
    else:
        print("No DMX library available, using simulation")
        interface = SimulatedDMXInterface()
    
    controller = DMXController(interface=interface, fps=fps)
    return controller, interface
