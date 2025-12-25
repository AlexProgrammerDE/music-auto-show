"""
Headless mode runner for Music Auto Show.
Runs the light show from a JSON configuration file without GUI.
"""
import argparse
import signal
import sys
import time
from pathlib import Path

from config import ShowConfig
from dmx_controller import create_dmx_controller
from spotify_analyzer import create_spotify_analyzer
from effects_engine import EffectsEngine


class HeadlessRunner:
    """
    Headless runner for Music Auto Show.
    """
    
    def __init__(self, config_path: str, simulate_dmx: bool = False, simulate_spotify: bool = False):
        self.config_path = config_path
        self.simulate_dmx = simulate_dmx
        self.simulate_spotify = simulate_spotify
        
        self.config = None
        self.dmx_controller = None
        self.dmx_interface = None
        self.spotify_analyzer = None
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
        print("Initializing DMX...")
        self.dmx_controller, self.dmx_interface = create_dmx_controller(
            port=self.config.dmx.port,
            simulate=self.simulate_dmx,
            fps=self.config.dmx.fps
        )
        
        if not self.dmx_interface.open():
            print("Failed to open DMX interface")
            return False
        
        if not self.dmx_controller.start():
            print("Failed to start DMX controller")
            return False
        
        print("DMX initialized" + (" (simulated)" if self.simulate_dmx else ""))
        
        # Initialize Spotify
        print("Initializing Spotify...")
        self.spotify_analyzer = create_spotify_analyzer(
            client_id=self.config.spotify.client_id,
            client_secret=self.config.spotify.client_secret,
            redirect_uri=self.config.spotify.redirect_uri,
            simulate=self.simulate_spotify
        )
        
        if not self.spotify_analyzer.start():
            print("Failed to start Spotify analyzer")
            return False
        
        print("Spotify initialized" + (" (simulated)" if self.simulate_spotify else ""))
        
        # Initialize effects engine
        self.effects_engine = EffectsEngine(
            self.dmx_controller,
            self.config.fixtures,
            self.config.effects
        )
        
        print("Effects engine initialized")
        print(f"Running in {self.config.effects.mode.value} mode...")
        
        return True
    
    def run(self) -> None:
        """Run the main loop."""
        self._running = True
        
        # Setup signal handlers for clean shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        last_status = time.time()
        
        while self._running:
            # Get analysis data and process
            data = self.spotify_analyzer.get_data()
            self.effects_engine.process(data)
            
            # Print status every 5 seconds
            now = time.time()
            if now - last_status >= 5.0:
                if data.track.is_playing:
                    print(f"Playing: {data.track.artist} - {data.track.name} "
                          f"| Energy: {data.features.energy:.2f} "
                          f"| Tempo: {data.features.tempo:.0f} BPM")
                else:
                    print("Waiting for playback...")
                last_status = now
            
            time.sleep(0.025)  # 40 Hz
    
    def stop(self) -> None:
        """Stop the light show."""
        self._running = False
        
        if self.effects_engine:
            self.effects_engine.blackout()
        
        if self.spotify_analyzer:
            self.spotify_analyzer.stop()
        
        if self.dmx_controller:
            self.dmx_controller.stop()
        
        if self.dmx_interface:
            self.dmx_interface.close()
        
        print("Show stopped")
    
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
    parser.add_argument("--simulate-spotify", action="store_true",
                       help="Simulate Spotify (no API required)")
    parser.add_argument("--simulate", action="store_true",
                       help="Simulate both DMX and Spotify")
    
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
    simulate_spotify = args.simulate_spotify or args.simulate
    
    runner = HeadlessRunner(args.config, simulate_dmx, simulate_spotify)
    
    if runner.start():
        try:
            runner.run()
        finally:
            runner.stop()
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
