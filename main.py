#!/usr/bin/env python3
"""
Music Auto Show - DMX Light Show Synchronized with Spotify

A cross-platform application that visualizes music from Spotify
to DMX-controlled lighting fixtures.

Usage:
    # GUI mode
    python main.py
    
    # Headless mode with config
    python main.py --headless config.json
    
    # Simulation mode (no hardware/API required)
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
        import spotipy
        deps['spotipy'] = True
    except ImportError:
        deps['spotipy'] = False
    
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
    
    return deps


def print_dependency_status(deps: dict) -> None:
    """Print dependency status."""
    print("Dependency Status:")
    print("-" * 40)
    
    required = ['pydantic', 'numpy']
    optional_dmx = ['pyftdi', 'pyserial']
    optional_spotify = ['spotipy']
    optional_gui = ['dearpygui']
    
    all_ok = True
    
    for dep in required:
        status = "OK" if deps.get(dep) else "MISSING (required)"
        if not deps.get(dep):
            all_ok = False
        print(f"  {dep}: {status}")
    
    print()
    print("DMX Support:")
    dmx_ok = any(deps.get(d) for d in optional_dmx)
    for dep in optional_dmx:
        status = "OK" if deps.get(dep) else "not installed"
        print(f"  {dep}: {status}")
    if not dmx_ok:
        print("  (Simulation mode will be used)")
    
    print()
    print("Spotify Support:")
    for dep in optional_spotify:
        status = "OK" if deps.get(dep) else "not installed"
        print(f"  {dep}: {status}")
    if not deps.get('spotipy'):
        print("  (Simulation mode will be used)")
    
    print()
    print("GUI Support:")
    for dep in optional_gui:
        status = "OK" if deps.get(dep) else "not installed"
        print(f"  {dep}: {status}")
    if not deps.get('dearpygui'):
        print("  (Only headless mode available)")
    
    print("-" * 40)
    
    if not all_ok:
        print("\nInstall missing dependencies with:")
        print("  pip install -r requirements.txt")
        print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Music Auto Show - DMX Light Show Synchronized with Spotify",
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
                       help="Simulate DMX and Spotify (no hardware/API)")
    parser.add_argument("--simulate-dmx", action="store_true",
                       help="Simulate DMX output only")
    parser.add_argument("--simulate-spotify", action="store_true",
                       help="Simulate Spotify only")
    parser.add_argument("--create-example", metavar="PATH",
                       help="Create example configuration file")
    parser.add_argument("--check-deps", action="store_true",
                       help="Check dependency status and exit")
    
    args = parser.parse_args()
    
    # Check dependencies
    deps = check_dependencies()
    
    if args.check_deps:
        print_dependency_status(deps)
        return
    
    # Check required dependencies
    if not deps.get('pydantic'):
        print("Error: pydantic is required. Install with: pip install pydantic")
        sys.exit(1)
    
    # Create example config
    if args.create_example:
        from headless import create_example_config
        create_example_config(args.create_example)
        return
    
    # Headless mode
    if args.headless or args.config:
        if not args.config:
            print("Error: Configuration file required for headless mode")
            sys.exit(1)
        
        from headless import HeadlessRunner
        
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
        return
    
    # GUI mode
    if not deps.get('dearpygui'):
        print("Error: Dear PyGui is required for GUI mode.")
        print("Install with: pip install dearpygui")
        print("Or use headless mode: python main.py --headless config.json")
        sys.exit(1)
    
    from gui import MusicAutoShowGUI
    
    app = MusicAutoShowGUI()
    
    # Apply simulation flags
    if args.simulate or args.simulate_dmx or args.simulate_spotify:
        # These will be applied when starting the show
        pass
    
    app.run()


if __name__ == "__main__":
    main()
