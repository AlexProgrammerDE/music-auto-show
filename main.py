#!/usr/bin/env python3
"""
Music Auto Show - DMX Light Show Synchronized with Music

A cross-platform application that visualizes music using real-time
audio analysis to control DMX lighting fixtures.

Usage:
    # GUI mode
    python main.py
    
    # Headless mode with config
    python main.py --headless config.json
    
    # Simulation mode (no hardware required)
    python main.py --simulate
    
    # Create example config
    python main.py --create-example example_config.json
"""
import argparse
import logging
import sys

# Configure logging early - before any other imports
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def check_dependencies() -> dict:
    """Check which dependencies are available."""
    deps = {}
    
    try:
        import dearpygui.dearpygui
        deps['dearpygui'] = True
    except ImportError:
        deps['dearpygui'] = False
    
    try:
        import pyftdi
        deps['pyftdi'] = True
    except ImportError:
        deps['pyftdi'] = False
    
    try:
        import serial
        deps['pyserial'] = True
    except ImportError:
        deps['pyserial'] = False
    
    try:
        import pydantic
        deps['pydantic'] = True
    except ImportError:
        deps['pydantic'] = False
    
    try:
        import numpy
        deps['numpy'] = True
    except ImportError:
        deps['numpy'] = False
    
    try:
        import madmom
        deps['madmom'] = True
    except ImportError:
        deps['madmom'] = False
    
    return deps


def print_dependency_status(deps: dict) -> None:
    """Print dependency status."""
    logger.info("Dependency Status:")
    logger.info("-" * 40)
    
    required = ['pydantic', 'numpy', 'madmom']
    optional_dmx = ['pyftdi', 'pyserial']
    optional_gui = ['dearpygui']
    
    all_ok = True
    
    for dep in required:
        status = "OK" if deps.get(dep) else "MISSING (required)"
        if not deps.get(dep):
            all_ok = False
        logger.info(f"  {dep}: {status}")
    
    logger.info("")
    logger.info("DMX Support:")
    dmx_ok = any(deps.get(d) for d in optional_dmx)
    for dep in optional_dmx:
        status = "OK" if deps.get(dep) else "not installed"
        logger.info(f"  {dep}: {status}")
    if not dmx_ok:
        logger.info("  (Simulation mode will be used)")
    
    logger.info("")
    logger.info("GUI Support:")
    for dep in optional_gui:
        status = "OK" if deps.get(dep) else "not installed"
        logger.info(f"  {dep}: {status}")
    if not deps.get('dearpygui'):
        logger.info("  (Only headless mode available)")
    
    logger.info("-" * 40)
    
    if not all_ok:
        logger.warning("Install missing dependencies with:")
        logger.warning("  pip install -r requirements.txt")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Music Auto Show - DMX Light Show Synchronized with Music",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          # Start GUI
  python main.py --headless config.json   # Run headless with config
  python main.py --simulate               # GUI with simulation
  python main.py --create-example out.json  # Create example config
  python main.py --check-deps             # Check dependencies
        """
    )
    
    parser.add_argument("config", nargs="?",
                       help="Configuration file (for headless mode)")
    parser.add_argument("--headless", action="store_true",
                       help="Run in headless mode (requires config file)")
    parser.add_argument("--simulate", action="store_true",
                       help="Simulate DMX and audio (no hardware required)")
    parser.add_argument("--simulate-dmx", action="store_true",
                       help="Simulate DMX output only")
    parser.add_argument("--simulate-audio", action="store_true",
                       help="Simulate audio input only")
    parser.add_argument("--microphone", "--mic", action="store_true",
                       help="Use microphone input instead of system audio loopback")
    parser.add_argument("--create-example", metavar="PATH",
                       help="Create example configuration file")
    parser.add_argument("--check-deps", action="store_true",
                       help="Check dependency status and exit")
    parser.add_argument("--list-audio-devices", action="store_true",
                       help="List available audio input devices and exit")
    parser.add_argument("--debug", action="store_true",
                       help="Enable debug logging")
    
    args = parser.parse_args()
    
    # Set debug logging if requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Check dependencies
    deps = check_dependencies()
    
    if args.check_deps:
        print_dependency_status(deps)
        return
    
    # List audio devices
    if args.list_audio_devices:
        from audio_analyzer import AudioAnalyzer
        analyzer = AudioAnalyzer()
        devices = analyzer.list_devices()
        logger.info("Available Audio Input Devices:")
        logger.info("-" * 60)
        for dev in devices:
            loopback_tag = " [LOOPBACK]" if dev.get('is_loopback') else ""
            logger.info(f"  [{dev['index']}] {dev['name']}{loopback_tag}")
            logger.info(f"      Channels: {dev['channels']}, Sample Rate: {dev['sample_rate']} Hz")
        logger.info("-" * 60)
        logger.info("Use --microphone to use microphone input instead of system loopback")
        return
    
    # Check required dependencies
    if not deps.get('pydantic'):
        logger.error("pydantic is required. Install with: pip install pydantic")
        sys.exit(1)
    
    # Create example config
    if args.create_example:
        from headless import create_example_config
        create_example_config(args.create_example)
        return
    
    # Headless mode
    if args.headless or args.config:
        if not args.config:
            logger.error("Configuration file required for headless mode")
            sys.exit(1)
        
        from headless import HeadlessRunner
        
        simulate_dmx = args.simulate_dmx or args.simulate
        simulate_audio = args.simulate_audio or args.simulate
        use_microphone = getattr(args, 'microphone', False)
        
        runner = HeadlessRunner(args.config, simulate_dmx, simulate_audio, use_microphone)
        if runner.start():
            try:
                runner.run()
            finally:
                runner.stop()
        else:
            sys.exit(1)
        return
    
    # GUI mode
    if not deps.get('dearpygui'):
        logger.error("Dear PyGui is required for GUI mode.")
        logger.error("Install with: pip install dearpygui")
        logger.error("Or use headless mode: python main.py --headless config.json")
        sys.exit(1)
    
    from gui import MusicAutoShowGUI
    
    app = MusicAutoShowGUI()
    app.run()


if __name__ == "__main__":
    main()
