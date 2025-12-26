"""
Headless mode runner for Music Auto Show.
Runs the light show from a JSON configuration file without GUI.
"""
import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from config import ShowConfig
from dmx_controller import create_dmx_controller, configure_logging as configure_dmx_logging
from audio_analyzer import create_audio_analyzer
from effects_engine import EffectsEngine

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class HeadlessRunner:
    """
    Headless runner for Music Auto Show.
    """
    
    def __init__(self, config_path: str, simulate_dmx: bool = False, simulate_audio: bool = False):
        self.config_path = config_path
        self.simulate_dmx = simulate_dmx
        self.simulate_audio = simulate_audio
        
        self.config = None
        self.dmx_controller = None
        self.dmx_interface = None
        self.audio_analyzer = None
        self.effects_engine = None
        
        self._running = False
    
    def load_config(self) -> bool:
        """Load configuration from file."""
        try:
            self.config = ShowConfig.load(self.config_path)
            print(f"Loaded config: {self.config.name}")
            print(f"  Fixtures: {len(self.config.fixtures)}")
            print(f"  Mode: {self.config.effects.mode.value}")
            return True
        except Exception as e:
            print(f"Failed to load config: {e}")
            return False
    
    def start(self) -> bool:
        """Start the light show."""
        if not self.config:
            if not self.load_config():
                return False
        
        # Initialize DMX
        logger.info("=" * 50)
        logger.info("STARTING MUSIC AUTO SHOW")
        logger.info("=" * 50)
        logger.info("")
        logger.info("Initializing DMX interface...")
        
        self.dmx_controller, self.dmx_interface = create_dmx_controller(
            port=self.config.dmx.port,
            simulate=self.simulate_dmx,
            fps=self.config.dmx.fps
        )
        
        if not self.dmx_interface.open():
            logger.error("Failed to open DMX interface!")
            logger.error("Check the following:")
            logger.error("  1. Is the USB adapter connected?")
            logger.error("  2. Do you have permission to access the serial port?")
            logger.error("     (On Linux: sudo usermod -a -G dialout $USER)")
            logger.error("  3. Is another application using the DMX adapter?")
            return False
        
        if not self.dmx_controller.start():
            logger.error("Failed to start DMX controller output loop")
            return False
        
        mode_str = "SIMULATED" if self.simulate_dmx else "HARDWARE"
        logger.info(f"DMX output active [{mode_str}]")
        
        # Initialize audio analyzer
        logger.info("")
        logger.info("Initializing audio capture...")
        self.audio_analyzer = create_audio_analyzer(simulate=self.simulate_audio)
        
        if not self.audio_analyzer.start():
            logger.error("Failed to start audio analyzer")
            return False
        
        mode_str = "SIMULATED" if self.simulate_audio else "LIVE"
        logger.info(f"Audio capture active [{mode_str}]")
        
        # Initialize effects engine
        self.effects_engine = EffectsEngine(
            self.dmx_controller,
            self.config
        )
        
        logger.info("")
        logger.info("Effects engine initialized")
        logger.info(f"  Mode: {self.config.effects.mode.value}")
        logger.info(f"  Fixtures: {len(self.config.fixtures)}")
        for fixture in self.config.fixtures:
            logger.info(f"    - {fixture.name} (ch {fixture.start_channel})")
        
        logger.info("")
        logger.info("=" * 50)
        logger.info("SHOW RUNNING - Press Ctrl+C to stop")
        logger.info("=" * 50)
        
        return True
    
    def run(self) -> None:
        """Run the main loop."""
        self._running = True
        
        # Setup signal handlers for clean shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        last_status = time.time()
        frame_count = 0
        
        while self._running:
            # Get analysis data and process
            data = self.audio_analyzer.get_data()
            self.effects_engine.process(data)
            frame_count += 1
            
            # Print status every 5 seconds
            now = time.time()
            if now - last_status >= 5.0:
                # Get DMX stats if available
                dmx_info = ""
                if self.dmx_controller and hasattr(self.dmx_controller, 'get_stats'):
                    stats = self.dmx_controller.get_stats()
                    dmx_info = f" | DMX: {stats.get('actual_fps', 0):.0f} FPS"
                    if 'interface' in stats:
                        iface = stats['interface']
                        if iface.get('error_count', 0) > 0:
                            dmx_info += f" ({iface['error_count']} errors)"
                
                logger.info(f"Energy: {data.features.energy:.2f} | "
                           f"Bass: {data.features.bass:.2f} | "
                           f"Tempo: {data.features.tempo:.0f} BPM{dmx_info}")
                last_status = now
            
            time.sleep(0.025)  # 40 Hz
    
    def stop(self) -> None:
        """Stop the light show."""
        self._running = False
        
        logger.info("")
        logger.info("Stopping show...")
        
        if self.effects_engine:
            logger.info("  Sending blackout...")
            self.effects_engine.blackout()
        
        if self.audio_analyzer:
            logger.info("  Stopping audio capture...")
            self.audio_analyzer.stop()
        
        if self.dmx_controller:
            # Log final stats
            if hasattr(self.dmx_controller, 'get_stats'):
                stats = self.dmx_controller.get_stats()
                logger.info(f"  DMX stats: {stats.get('frame_count', 0)} frames sent, "
                           f"{stats.get('actual_fps', 0):.1f} FPS average")
            logger.info("  Stopping DMX output...")
            self.dmx_controller.stop()
        
        if self.dmx_interface:
            logger.info("  Closing DMX interface...")
            self.dmx_interface.close()
        
        logger.info("")
        logger.info("Show stopped")
    
    def _signal_handler(self, signum, frame) -> None:
        """Handle shutdown signals."""
        print("\nShutting down...")
        self._running = False


def create_example_config(output_path: str) -> None:
    """Create an example configuration file."""
    from config import ChannelConfig, ChannelType, VisualizationMode
    
    config = ShowConfig(
        name="Example Show",
        fixtures=[
            FixtureConfig(
                name="Par 1",
                start_channel=1,
                position=0,
                channels=[
                    ChannelConfig(channel=1, channel_type=ChannelType.RED),
                    ChannelConfig(channel=2, channel_type=ChannelType.GREEN),
                    ChannelConfig(channel=3, channel_type=ChannelType.BLUE),
                    ChannelConfig(channel=4, channel_type=ChannelType.DIMMER),
                ]
            ),
            FixtureConfig(
                name="Par 2",
                start_channel=5,
                position=1,
                channels=[
                    ChannelConfig(channel=5, channel_type=ChannelType.RED),
                    ChannelConfig(channel=6, channel_type=ChannelType.GREEN),
                    ChannelConfig(channel=7, channel_type=ChannelType.BLUE),
                    ChannelConfig(channel=8, channel_type=ChannelType.DIMMER),
                ]
            ),
            FixtureConfig(
                name="Moving Head",
                start_channel=9,
                position=2,
                pan_min=0,
                pan_max=255,
                tilt_min=0,
                tilt_max=180,
                channels=[
                    ChannelConfig(channel=9, channel_type=ChannelType.PAN),
                    ChannelConfig(channel=10, channel_type=ChannelType.TILT),
                    ChannelConfig(channel=11, channel_type=ChannelType.RED),
                    ChannelConfig(channel=12, channel_type=ChannelType.GREEN),
                    ChannelConfig(channel=13, channel_type=ChannelType.BLUE),
                    ChannelConfig(channel=14, channel_type=ChannelType.DIMMER),
                    ChannelConfig(channel=15, channel_type=ChannelType.SPEED),
                    ChannelConfig(channel=16, channel_type=ChannelType.STROBE),
                ]
            ),
        ]
    )
    
    config.effects.mode = VisualizationMode.RAINBOW_WAVE
    config.effects.intensity = 0.8
    config.effects.movement_enabled = True
    
    config.save(output_path)
    print(f"Example config saved to: {output_path}")


def main():
    """Main entry point for headless mode."""
    parser = argparse.ArgumentParser(description="Music Auto Show - Headless Mode")
    parser.add_argument("config", nargs="?", help="Path to configuration JSON file")
    parser.add_argument("--create-example", metavar="PATH",
                       help="Create example configuration file")
    parser.add_argument("--simulate-dmx", action="store_true",
                       help="Simulate DMX output (no hardware required)")
    parser.add_argument("--simulate-audio", action="store_true",
                       help="Simulate audio input (no capture required)")
    parser.add_argument("--simulate", action="store_true",
                       help="Simulate both DMX and audio")
    
    args = parser.parse_args()
    
    if args.create_example:
        from config import FixtureConfig
        create_example_config(args.create_example)
        return
    
    if not args.config:
        parser.print_help()
        print("\nError: Configuration file required")
        sys.exit(1)
    
    if not Path(args.config).exists():
        print(f"Error: Configuration file not found: {args.config}")
        sys.exit(1)
    
    simulate_dmx = args.simulate_dmx or args.simulate
    simulate_audio = args.simulate_audio or args.simulate
    
    runner = HeadlessRunner(args.config, simulate_dmx, simulate_audio)
    
    if runner.start():
        try:
            runner.run()
        finally:
            runner.stop()
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
