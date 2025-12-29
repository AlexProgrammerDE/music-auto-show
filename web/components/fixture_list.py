"""
Fixture list component for displaying and managing fixtures.
"""
from nicegui import ui

from config import FixtureConfig, get_available_presets, get_preset
from web.state import app_state
from web.components.fixture_dialogs import FixtureDialogs


class FixtureList:
    """Fixture list component with add/edit/remove functionality."""
    
    def __init__(self):
        self.dialogs = FixtureDialogs(on_change=self._refresh_list)
        self._list_container = None
        self._create_ui()
    
    def _create_ui(self) -> None:
        """Create the fixture list UI."""
        with ui.expansion('Fixtures', icon='lightbulb', value=True).classes('w-full'):
            with ui.column().classes('w-full gap-3 p-3'):
                # Action buttons
                with ui.row().classes('gap-2 w-full'):
                    ui.button('Add Fixture', on_click=self.dialogs.show_add_dialog, icon='add', color='green').props('flat dense').classes('flex-grow')
                    ui.button('Remove', on_click=self._remove_selected, icon='delete', color='red').props('flat dense')
                
                # Fixture list
                self._list_container = ui.column().classes('w-full gap-2')
                self._refresh_list()
    
    def _refresh_list(self) -> None:
        """Refresh the fixture list display."""
        if self._list_container is None:
            return
        
        self._list_container.clear()
        
        with self._list_container:
            if not app_state.config.fixtures:
                with ui.column().classes('w-full items-center py-4 gap-2'):
                    ui.icon('lightbulb_outline').classes('text-4xl text-gray-500 opacity-40')
                    ui.label('No fixtures configured').classes('italic text-gray-500')
                    ui.label('Click "Add Fixture" to get started').classes('text-xs text-gray-600')
                return
            
            for i, fixture in enumerate(app_state.config.fixtures):
                self._create_fixture_item(i, fixture)
    
    def _create_fixture_item(self, index: int, fixture: FixtureConfig) -> None:
        """Create a fixture list item."""
        profile_text = fixture.profile_name if fixture.profile_name else "Custom"
        
        with ui.card().classes('w-full cursor-pointer').props('flat bordered').on('click', lambda _e, f=fixture: self.dialogs.show_edit_dialog(f)):
            with ui.row().classes('items-center justify-between w-full gap-3'):
                # Fixture icon
                ui.icon('lightbulb').classes('text-primary text-2xl')
                
                # Fixture info
                with ui.column().classes('flex-grow gap-0'):
                    ui.label(fixture.name).classes('font-semibold')
                    ui.label(f'{profile_text}').classes('text-xs text-gray-500')
                    ui.label(f'Channel {fixture.start_channel}').classes('text-xs text-primary font-mono')
                
                # Color indicator (shows current state if running)
                color_indicator = ui.element('div').classes('w-5 h-5 rounded-full bg-gray-700')
                
                def update_color(name=fixture.name, indicator=color_indicator):
                    state = app_state.get_fixture_state(name)
                    if state.dimmer > 0:
                        r, g, b = state.red, state.green, state.blue
                        indicator.style(f'background-color: rgb({r},{g},{b}); box-shadow: 0 0 8px rgb({r},{g},{b})')
                    else:
                        indicator.style('background-color: #374151; box-shadow: none')
                
                ui.timer(0.1, update_color)
    
    def _remove_selected(self) -> None:
        """Remove the last fixture (simplified - could add selection)."""
        if app_state.config.fixtures:
            removed = app_state.config.fixtures.pop()
            ui.notify(f'Removed: {removed.name}', type='info')
            self._refresh_list()
            app_state.update_effects_config()
