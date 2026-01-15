# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for music-auto-show.
Builds a single-file executable for the DMX light show application.
"""

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect data files from packages that need them
madmom_datas = collect_data_files('madmom')
nicegui_datas = collect_data_files('nicegui')

# Hidden imports that PyInstaller may not detect
hiddenimports = [
    # Local application modules
    'web',
    'web.app',
    'web.state',
    'web.components',
    'web.components.audio_meters',
    'web.components.audio_visualizer',
    'web.components.config_panel',
    'web.components.dmx_universe',
    'web.components.effects_panel',
    'web.components.fixture_dialogs',
    'web.components.fixture_list',
    'web.components.stage_view',
    'config',
    'audio_analyzer',
    'audio_devices',
    'dmx_controller',
    'effects_engine',
    'media_info',
    'movement_modes',
    'simulators',
    'visualization_modes',

    # Madmom beat detection
    'madmom',
    'madmom.features',
    'madmom.features.beats',
    'madmom.features.onsets',
    'madmom.features.tempo',
    'madmom.audio',
    'madmom.audio.signal',
    'madmom.ml',
    'madmom.ml.nn',
    'madmom.processors',

    # NiceGUI and its dependencies
    'nicegui',
    'nicegui.elements',
    'nicegui.app',
    'nicegui.ui',
    'engineio.async_drivers.aiohttp',
    'aiohttp',
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',

    # Pydantic
    'pydantic',
    'pydantic.fields',
    'pydantic_core',

    # Serial/DMX
    'serial',
    'serial.tools',
    'serial.tools.list_ports',
    'serial.tools.list_ports_common',
    'serial.tools.list_ports_linux',
    'serial.tools.list_ports_osx',
    'serial.tools.list_ports_windows',

    # FTDI
    'pyftdi',
    'pyftdi.ftdi',
    'pyftdi.usbtools',

    # Image processing
    'PIL',
    'PIL.Image',

    # Numpy
    'numpy',
    'numpy.core',
]

# Platform-specific hidden imports
if sys.platform == 'win32':
    hiddenimports += [
        'pyaudiowpatch',
        'winrt',
        'winrt.windows',
        'winrt.windows.media',
        'winrt.windows.media.control',
        'winrt.windows.foundation',
        'winrt.windows.storage',
        'winrt.windows.storage.streams',
    ]
elif sys.platform == 'linux':
    hiddenimports += [
        'pyaudio',
        'dbus',
        'dbus.mainloop',
        'dbus.mainloop.glib',
    ]
elif sys.platform == 'darwin':
    hiddenimports += [
        'pyaudio',
    ]

# Collect all submodules for complex packages
hiddenimports += collect_submodules('madmom')
hiddenimports += collect_submodules('nicegui')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=madmom_datas + nicegui_datas + [
        ('example_config.json', '.'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'scipy',
        'pandas',
        'sklearn',
        'tkinter',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='music-auto-show',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
