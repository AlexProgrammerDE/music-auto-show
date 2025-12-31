"""
Audio visualizer component with beat circle, spectrum, and onset history.
Recreates the DearPyGui visualizations using NiceGUI canvas.
"""
import math
import time
from nicegui import ui

from web.state import app_state


class AudioVisualizer:
    """
    Audio visualization component with multiple displays:
    - Frequency spectrum (FFT visualization)
    - Onset detection history (beat detection over time)
    - Frequency bands (Bass/Mid/High bars)
    - Beat pulse circle (circular beat indicator)
    """
    
    # Layout constants
    TOTAL_WIDTH = 500
    TOTAL_HEIGHT = 100
    
    # Section widths
    SPECTRUM_WIDTH = 150
    ONSET_WIDTH = 150
    BANDS_WIDTH = 100
    PULSE_WIDTH = 100
    
    def __init__(self):
        self._canvas = None
        self._create_ui()
    
    def _create_ui(self) -> None:
        """Create the visualizer UI with canvas."""
        # Container with dark background
        with ui.card().classes('w-full p-0 overflow-hidden').style('background: #0f0f16'):
            # Section labels
            with ui.row().classes('w-full justify-between px-2 py-1').style('background: #1a1a24'):
                ui.label('Spectrum').classes('text-xs text-gray-500')
                ui.label('Onset History').classes('text-xs text-gray-500')
                ui.label('Bands').classes('text-xs text-gray-500')
                ui.label('Beat').classes('text-xs text-gray-500')
            
            # Canvas for drawing (sanitize=False since we generate the SVG ourselves)
            self._canvas = ui.html('', sanitize=False).classes('w-full')
            self._canvas.style(f'height: {self.TOTAL_HEIGHT}px')
        
        # Update timer - 20 FPS for smooth animation
        ui.timer(0.05, self._update_canvas)
    
    def _update_canvas(self) -> None:
        """Update the canvas with current audio state."""
        state = app_state.audio_state
        
        # Build SVG content
        svg = self._build_svg(state)
        self._canvas.content = svg
    
    def _build_svg(self, state) -> str:
        """Build the complete SVG visualization."""
        w = self.TOTAL_WIDTH
        h = self.TOTAL_HEIGHT
        
        # Start SVG
        svg = f'''<svg width="100%" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet" 
                   style="background: #0f0f16;">'''
        
        # Draw sections
        svg += self._draw_spectrum(state, 0, 0, self.SPECTRUM_WIDTH, h)
        svg += self._draw_separator(self.SPECTRUM_WIDTH, h)
        
        svg += self._draw_onset_history(state, self.SPECTRUM_WIDTH + 2, 0, self.ONSET_WIDTH - 4, h)
        svg += self._draw_separator(self.SPECTRUM_WIDTH + self.ONSET_WIDTH, h)
        
        svg += self._draw_frequency_bands(state, self.SPECTRUM_WIDTH + self.ONSET_WIDTH + 2, 0, self.BANDS_WIDTH - 4, h)
        svg += self._draw_separator(self.SPECTRUM_WIDTH + self.ONSET_WIDTH + self.BANDS_WIDTH, h)
        
        svg += self._draw_beat_pulse(state, self.SPECTRUM_WIDTH + self.ONSET_WIDTH + self.BANDS_WIDTH, 0, self.PULSE_WIDTH, h)
        
        svg += '</svg>'
        return svg
    
    def _draw_separator(self, x: float, height: float) -> str:
        """Draw a vertical separator line."""
        return f'<line x1="{x}" y1="0" x2="{x}" y2="{height}" stroke="#32324a" stroke-width="1"/>'
    
    def _draw_spectrum(self, state, x: float, y: float, width: float, height: float) -> str:
        """Draw frequency spectrum visualization."""
        svg = ''
        
        spectrum = state.spectrum if state.spectrum else []
        bass, mid, high = state.bass, state.mid, state.high
        beat_pos = state.beat_position
        
        if spectrum and len(spectrum) > 0:
            # Use actual spectrum data
            num_bands = len(spectrum)
            bar_width = max(2, width / num_bands)
            
            for i, value in enumerate(spectrum):
                bx = x + i * bar_width
                bar_h = value * (height - 4)
                
                # Color gradient: blue -> cyan -> green -> yellow -> red
                pos = i / max(1, num_bands - 1)
                r, g, b = self._spectrum_color(pos)
                
                if bar_h > 0.5:
                    svg += f'''<rect x="{bx:.1f}" y="{height - bar_h - 2:.1f}" 
                               width="{bar_width - 1:.1f}" height="{bar_h:.1f}" 
                               fill="rgba({r},{g},{b},0.85)"/>'''
        else:
            # Fallback: generate from bass/mid/high
            num_bands = 24
            bar_width = width / num_bands
            
            for i in range(num_bands):
                pos = i / (num_bands - 1)
                
                # Blend values based on position
                if pos < 0.33:
                    value = bass * (1 - pos * 3) + mid * (pos * 3)
                elif pos < 0.66:
                    value = mid * (1 - (pos - 0.33) * 3) + high * ((pos - 0.33) * 3)
                else:
                    value = high * (1 - (pos - 0.66) * 2)
                
                # Add variation with beat
                value *= 0.7 + 0.3 * math.sin(i * 0.8 + beat_pos * 6.28)
                
                bx = x + i * bar_width
                bar_h = max(2, value * (height - 4))
                
                r, g, b = self._spectrum_color(pos)
                
                svg += f'''<rect x="{bx:.1f}" y="{height - bar_h - 2:.1f}" 
                           width="{bar_width - 1:.1f}" height="{bar_h:.1f}" 
                           fill="rgba({r},{g},{b},0.8)"/>'''
        
        return svg
    
    def _spectrum_color(self, pos: float) -> tuple[int, int, int]:
        """Get spectrum color for position (0-1)."""
        if pos < 0.33:
            return (50, int(100 + pos * 3 * 155), 255)
        elif pos < 0.66:
            p = (pos - 0.33) * 3
            return (int(p * 255), 255, int(255 * (1 - p)))
        else:
            p = (pos - 0.66) * 3
            return (255, int(255 * (1 - p)), 50)
    
    def _draw_onset_history(self, state, x: float, y: float, width: float, height: float) -> str:
        """Draw onset detection history graph."""
        svg = ''
        
        onset_data = state.onset_history if state.onset_history else []
        energy = state.energy
        beat_pos = state.beat_position
        
        if onset_data and len(onset_data) > 0:
            # Draw actual onset history as line graph
            num_points = len(onset_data)
            point_width = width / num_points
            
            # Build path for filled area
            path_data = f'M {x} {height - 2}'
            
            for i, value in enumerate(onset_data):
                px = x + i * point_width
                py = height - 2 - value * (height - 8)
                path_data += f' L {px:.1f} {py:.1f}'
            
            path_data += f' L {x + width} {height - 2} Z'
            
            # Draw filled area
            svg += f'<path d="{path_data}" fill="rgba(100,180,255,0.3)"/>'
            
            # Draw line on top
            line_path = ''
            for i, value in enumerate(onset_data):
                px = x + i * point_width
                py = height - 2 - value * (height - 8)
                if i == 0:
                    line_path = f'M {px:.1f} {py:.1f}'
                else:
                    line_path += f' L {px:.1f} {py:.1f}'
            
            svg += f'<path d="{line_path}" fill="none" stroke="rgba(100,200,255,1)" stroke-width="2"/>'
        else:
            # Fallback: show energy-based wave with beat pulses
            num_points = 32
            point_width = width / num_points
            
            for i in range(num_points):
                pos = i / num_points
                
                # Create wave with beat-synced peaks
                wave = 0.3 + 0.4 * math.sin(pos * 12.56 + beat_pos * 6.28)
                wave *= energy
                
                # Add decay from recent "beat"
                if pos > 0.8:
                    wave += (1 - beat_pos) * 0.5 * ((pos - 0.8) / 0.2)
                
                bx = x + i * point_width
                bar_h = max(2, wave * (height - 8))
                
                svg += f'''<rect x="{bx:.1f}" y="{height - bar_h - 2:.1f}" 
                           width="{point_width - 1:.1f}" height="{bar_h:.1f}" 
                           fill="rgba(100,180,255,0.6)"/>'''
            
            # Draw threshold line
            threshold_y = height - 2 - 0.3 * (height - 8)
            svg += f'''<line x1="{x}" y1="{threshold_y:.1f}" x2="{x + width}" y2="{threshold_y:.1f}" 
                       stroke="rgba(255,100,100,0.6)" stroke-width="1" stroke-dasharray="4,2"/>'''
        
        return svg
    
    def _draw_frequency_bands(self, state, x: float, y: float, width: float, height: float) -> str:
        """Draw Bass/Mid/High frequency band bars."""
        svg = ''
        
        bass = state.bass
        mid = state.mid
        high = state.high
        
        bar_width = (width - 10) / 3
        bar_gap = 5
        max_height = height - 8
        
        # Bass bar (red/orange)
        bass_h = bass * max_height
        bass_x = x
        svg += f'''<rect x="{bass_x:.1f}" y="{height - bass_h - 4:.1f}" 
                   width="{bar_width:.1f}" height="{bass_h:.1f}" 
                   fill="rgba(255,80,50,0.9)" rx="2"/>'''
        svg += f'''<text x="{bass_x + bar_width/2:.1f}" y="{height - 1:.1f}" 
                   font-size="8" fill="#888" text-anchor="middle">B</text>'''
        
        # Mid bar (green/yellow)
        mid_h = mid * max_height
        mid_x = x + bar_width + bar_gap
        svg += f'''<rect x="{mid_x:.1f}" y="{height - mid_h - 4:.1f}" 
                   width="{bar_width:.1f}" height="{mid_h:.1f}" 
                   fill="rgba(150,255,50,0.9)" rx="2"/>'''
        svg += f'''<text x="{mid_x + bar_width/2:.1f}" y="{height - 1:.1f}" 
                   font-size="8" fill="#888" text-anchor="middle">M</text>'''
        
        # High bar (cyan/blue)
        high_h = high * max_height
        high_x = x + 2 * (bar_width + bar_gap)
        svg += f'''<rect x="{high_x:.1f}" y="{height - high_h - 4:.1f}" 
                   width="{bar_width:.1f}" height="{high_h:.1f}" 
                   fill="rgba(50,200,255,0.9)" rx="2"/>'''
        svg += f'''<text x="{high_x + bar_width/2:.1f}" y="{height - 1:.1f}" 
                   font-size="8" fill="#888" text-anchor="middle">H</text>'''
        
        return svg
    
    def _draw_beat_pulse(self, state, x: float, y: float, width: float, height: float) -> str:
        """Draw beat pulse circle with position arc."""
        svg = ''
        
        beat_pos = state.beat_position
        
        center_x = x + width / 2
        center_y = height / 2
        
        # Pulse size: large at beat start, shrinks through beat
        max_radius = min(width, height) / 2 - 8
        pulse_radius = max_radius * (1.0 - beat_pos * 0.6)
        
        # Intensity based on beat position
        intensity = 1.0 - beat_pos * 0.7
        
        # Outer glow
        if pulse_radius > 5:
            glow_opacity = intensity * 0.4
            svg += f'''<circle cx="{center_x:.1f}" cy="{center_y:.1f}" r="{pulse_radius + 4:.1f}" 
                       fill="rgba(100,200,100,{glow_opacity:.2f})"/>'''
        
        # Main pulse circle
        fill_opacity = intensity * 0.8
        stroke_opacity = intensity
        svg += f'''<circle cx="{center_x:.1f}" cy="{center_y:.1f}" r="{pulse_radius:.1f}" 
                   fill="rgba(100,255,100,{fill_opacity:.2f})" 
                   stroke="rgba(200,255,200,{stroke_opacity:.2f})" stroke-width="2"/>'''
        
        # Beat position arc (shows progress through beat)
        arc_radius = max_radius + 6
        arc_angle = beat_pos * 360
        
        if arc_angle > 5:
            # Calculate arc path (starting from top, going clockwise)
            start_angle = -90  # Start from top
            end_angle = start_angle + arc_angle
            
            # Convert to radians
            start_rad = math.radians(start_angle)
            end_rad = math.radians(end_angle)
            
            # Calculate start and end points
            start_x = center_x + arc_radius * math.cos(start_rad)
            start_y = center_y + arc_radius * math.sin(start_rad)
            end_x = center_x + arc_radius * math.cos(end_rad)
            end_y = center_y + arc_radius * math.sin(end_rad)
            
            # Large arc flag
            large_arc = 1 if arc_angle > 180 else 0
            
            # Build arc path
            arc_path = f'M {start_x:.1f} {start_y:.1f} A {arc_radius:.1f} {arc_radius:.1f} 0 {large_arc} 1 {end_x:.1f} {end_y:.1f}'
            
            svg += f'''<path d="{arc_path}" fill="none" stroke="rgba(255,200,100,0.9)" 
                       stroke-width="3" stroke-linecap="round"/>'''
        
        # BPM text in center
        tempo = state.tempo
        svg += f'''<text x="{center_x:.1f}" y="{center_y + 3:.1f}" 
                   font-size="11" font-weight="bold" fill="rgba(255,255,255,0.8)" 
                   text-anchor="middle">{tempo:.0f}</text>'''
        
        return svg
