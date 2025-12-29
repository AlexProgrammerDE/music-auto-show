#!/usr/bin/env python3
"""
Music Auto Show - DMX Light Show Synchronized with Music

A cross-platform application that visualizes music using real-time
audio analysis to control DMX lighting fixtures.

Usage:
    # Start web UI
    python main.py
    
    # Simulation mode (no hardware required)
    python main.py --simulate
    
    # Load config on startup
    python main.py --config config.json
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
        import nicegui
        deps['nicegui'] = True
    except ImportError:
        deps['nicegui'] = False
    
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
    
    required = ['pydantic', 'numpy', 'madmom', 'nicegui']
    optional_dmx = ['pyftdi', 'pyserial']
    
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
  python main.py                          # Start web UI
  python main.py --simulate               # Web UI with simulation
  python main.py --config show.json       # Load config on startup
  python main.py --check-deps             # Check dependencies
        """
    )
    
    parser.add_argument("--config", "-c",
                       help="Configuration file to load on startup")
    parser.add_argument("--simulate", action="store_true",
                       help="Simulate DMX and audio (no hardware required)")
    parser.add_argument("--simulate-dmx", action="store_true",
                       help="Simulate DMX output only")
    parser.add_argument("--simulate-audio", action="store_true",
                       help="Simulate audio input only")
    parser.add_argument("--auto-start", action="store_true",
                       help="Automatically start the show on launch")
    parser.add_argument("--check-deps", action="store_true",
                       help="Check dependency status and exit")
    parser.add_argument("--list-audio-devices", action="store_true",
                       help="List available audio input devices and exit")
    parser.add_argument("--debug", action="store_true",
                       help="Enable debug logging")
    parser.add_argument("--port", type=int, default=8080,
                       help="Port for web UI (default: 8080)")
    parser.add_argument("--host", default="0.0.0.0",
                       help="Host for web UI (default: 0.0.0.0)")
    
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
        from audio_devices import print_device_list
        print_device_list()
        return
    
    # Check required dependencies
    if not deps.get('pydantic'):
        logger.error("pydantic is required. Install with: pip install pydantic")
        sys.exit(1)
    
    if not deps.get('nicegui'):
        logger.error("nicegui is required. Install with: pip install nicegui")
        sys.exit(1)
    
    # Launch NiceGUI web app
    from nicegui import ui
    from web.app import create_app
    from web.state import app_state
    
    # Configure simulation mode
    if args.simulate or args.simulate_dmx:
        app_state.simulate_dmx = True
    if args.simulate or args.simulate_audio:
        app_state.simulate_audio = True
    
    # Create and run app
    create_app(
        config_path=args.config,
        simulate=args.simulate,
        auto_start=args.auto_start
    )
    
    logger.info(f"Starting Music Auto Show web UI on http://{args.host}:{args.port}")
    ui.run(
        host=args.host,
        port=args.port,
        title="Music Auto Show",
        reload=False,
        show=True
    )


if __name__ == "__main__":
    main()
