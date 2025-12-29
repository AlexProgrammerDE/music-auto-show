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
            with ui.column().classes('w-full gap-2 p-2'):
                # Action buttons
                with ui.row().classes('gap-2'):
                    ui.button('Add', on_click=self.dialogs.show_add_dialog, icon='add').props('flat dense')
                    ui.button('Remove', on_click=self._remove_selected, icon='delete').props('flat dense color=negative')
                
                # Fixture list
                self._list_container = ui.column().classes('w-full gap-1')
                self._refresh_list()
    
    def _refresh_list(self) -> None:
        """Refresh the fixture list display."""
        if self._list_container is None:
            return
        
        self._list_container.clear()
        
        with self._list_container:
            if not app_state.config.fixtures:
                ui.label('No fixtures configured').classes('text-gray-400 italic')
                return
            
            for i, fixture in enumerate(app_state.config.fixtures):
                self._create_fixture_item(i, fixture)
    
    def _create_fixture_item(self, index: int, fixture: FixtureConfig) -> None:
        """Create a fixture list item."""
        profile_text = fixture.profile_name if fixture.profile_name else "Custom"
        
        with ui.card().classes('w-full p-2 cursor-pointer').style('background: rgba(30, 35, 42, 0.9) !important; border: 1px solid rgba(55, 65, 81, 0.5);').on('click', lambda f=fixture: self.dialogs.show_edit_dialog(f)):
            with ui.row().classes('items-center justify-between w-full'):
                with ui.column().classes('gap-0'):
                    ui.label(fixture.name).classes('font-semibold')
                    ui.label(f'{profile_text} | Ch {fixture.start_channel}').classes('text-xs text-gray-400')
                
                # Color indicator (shows current state if running)
                off_style = 'width:16px;height:16px;border-radius:50%;background:#0d1117;border:1px solid rgba(55,65,81,0.6);'
                color_indicator = ui.html(f'<div style="{off_style}"></div>', sanitize=False)
                
                def update_color(name=fixture.name, indicator=color_indicator, off_style=off_style):
                    state = app_state.get_fixture_state(name)
                    if state.dimmer > 0:
                        r, g, b = state.red, state.green, state.blue
                        indicator.content = f'<div style="width:16px;height:16px;border-radius:50%;background:rgb({r},{g},{b});box-shadow:0 0 8px rgb({r},{g},{b});"></div>'
                    else:
                        indicator.content = f'<div style="{off_style}"></div>'
                
                ui.timer(0.1, update_color)
    
    def _remove_selected(self) -> None:
        """Remove the last fixture (simplified - could add selection)."""
        if app_state.config.fixtures:
            removed = app_state.config.fixtures.pop()
            ui.notify(f'Removed: {removed.name}', type='info')
            self._refresh_list()
            app_state.update_effects_config()
