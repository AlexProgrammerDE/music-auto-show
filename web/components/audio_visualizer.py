"""
Audio visualizer component with a 30 second spectrogram and beat context.
Uses a single canvas so the browser updates pixels instead of thousands of SVG nodes.
"""
import json
from typing import Any

from nicegui import ui

from web.state import app_state


class AudioVisualizer:
    """Audacity-style diagnostic audio visualization."""

    TOTAL_WIDTH = 860
    TOTAL_HEIGHT = 275
    PLOT_X = 48
    PLOT_Y = 12
    PLOT_WIDTH = 570
    PLOT_HEIGHT = 190
    BEAT_X = 642
    BEAT_Y = 12
    BEAT_WIDTH = 190
    BEAT_HEIGHT = 246
    WAVEFORM_Y = 224
    WAVEFORM_HEIGHT = 34

    MAX_COLUMNS = 140
    MAX_BINS = 48

    def __init__(self):
        self._canvas_id = f'audio-viz-{id(self)}'
        self._html = None
        self._create_ui()

    def _create_ui(self) -> None:
        """Create the visualizer canvas."""
        ui.add_body_html(self._draw_script())
        self._html = ui.html(self._canvas_html(), sanitize=False).classes('w-full')
        self._html.style(f'height: {self.TOTAL_HEIGHT}px;background:#111111;')
        ui.timer(0.1, self._update_canvas)
        self._update_canvas()

    def _update_canvas(self) -> None:
        """Send a compact payload to the browser canvas."""
        payload = self._build_payload()
        ui.run_javascript(
            f'if (window.__musicAutoShowDrawAudioViz) '
            f'window.__musicAutoShowDrawAudioViz({json.dumps(self._canvas_id)}, {json.dumps(payload)});'
        )

    def _build_payload(self) -> dict[str, Any]:
        state = app_state.audio_state
        return {
            'width': self.TOTAL_WIDTH,
            'height': self.TOTAL_HEIGHT,
            'plotX': self.PLOT_X,
            'plotY': self.PLOT_Y,
            'plotWidth': self.PLOT_WIDTH,
            'plotHeight': self.PLOT_HEIGHT,
            'beatX': self.BEAT_X,
            'beatY': self.BEAT_Y,
            'beatWidth': self.BEAT_WIDTH,
            'beatHeight': self.BEAT_HEIGHT,
            'waveformY': self.WAVEFORM_Y,
            'waveformHeight': self.WAVEFORM_HEIGHT,
            'frames': self._sample_frames(state.spectrogram),
            'waveform': self._quantize_values(state.waveform[-140:]),
            'energy': self._quantize_value(state.energy),
            'bass': self._quantize_value(state.bass),
            'mid': self._quantize_value(state.mid),
            'high': self._quantize_value(state.high),
            'tempo': round(state.tempo),
            'beatPosition': self._quantize_value(state.beat_position),
        }

    def _sample_frames(self, frames: list[list[float]]) -> list[list[int]]:
        if not frames:
            return []

        sampled_frames: list[list[float]]
        if len(frames) <= self.MAX_COLUMNS:
            sampled_frames = frames
        else:
            step = len(frames) / self.MAX_COLUMNS
            sampled_frames = [frames[int(i * step)] for i in range(self.MAX_COLUMNS)]

        return [self._sample_bins(frame) for frame in sampled_frames]

    def _sample_bins(self, frame: list[float]) -> list[int]:
        if not frame:
            return []
        if len(frame) <= self.MAX_BINS:
            return self._quantize_values(frame)

        step = len(frame) / self.MAX_BINS
        return [self._quantize_value(frame[int(i * step)]) for i in range(self.MAX_BINS)]

    def _quantize_values(self, values: list[float]) -> list[int]:
        return [self._quantize_value(value) for value in values]

    def _quantize_value(self, value: float) -> int:
        return max(0, min(255, int(value * 255)))

    def _canvas_html(self) -> str:
        return f'''
            <canvas
                id="{self._canvas_id}"
                width="{self.TOTAL_WIDTH}"
                height="{self.TOTAL_HEIGHT}"
                style="display:block;width:100%;height:{self.TOTAL_HEIGHT}px;background:#111111"
            ></canvas>
        '''

    def _draw_script(self) -> str:
        return '''
            <script>
            window.__musicAutoShowDrawAudioViz = window.__musicAutoShowDrawAudioViz || function(canvasId, payload) {
                const canvas = document.getElementById(canvasId);
                if (!canvas) return;
                const ctx = canvas.getContext('2d', { alpha: false });
                if (!ctx) return;

                const W = payload.width;
                const H = payload.height;
                const plotX = payload.plotX;
                const plotY = payload.plotY;
                const plotW = payload.plotWidth;
                const plotH = payload.plotHeight;
                const beatX = payload.beatX;
                const beatY = payload.beatY;
                const beatW = payload.beatWidth;
                const beatH = payload.beatHeight;
                const waveformY = payload.waveformY;
                const waveformH = payload.waveformHeight;
                const frames = payload.frames || [];

                ctx.clearRect(0, 0, W, H);
                ctx.fillStyle = '#111111';
                ctx.fillRect(0, 0, W, H);
                ctx.fillStyle = '#151515';
                ctx.fillRect(plotX, plotY, plotW, plotH);
                ctx.fillRect(plotX, waveformY, plotW, waveformH);
                ctx.fillRect(beatX, beatY, beatW, beatH);

                drawGrid(ctx, plotX, plotY, plotW, plotH);
                if (frames.length > 0) {{
                    drawSpectrogram(ctx, frames, plotX, plotY, plotW, plotH);
                }} else {{
                    drawEmpty(ctx, plotX, plotY, plotW, plotH);
                }}
                drawAxes(ctx, plotX, plotY, plotW, plotH);
                drawWaveform(ctx, payload.waveform || [], plotX, waveformY, plotW, waveformH);
                drawBeatPanel(ctx, payload, beatX, beatY, beatW);
            };

            function drawGrid(ctx, x, y, w, h) {
                ctx.strokeStyle = '#303030';
                ctx.lineWidth = 1;
                for (const offset of [0, 0.333, 0.667, 1]) {
                    const gx = x + w * offset;
                    ctx.beginPath();
                    ctx.moveTo(gx, y);
                    ctx.lineTo(gx, y + h);
                    ctx.stroke();
                }
            }

            function drawSpectrogram(ctx, frames, x, y, w, h) {
                const columns = frames.length;
                const bins = frames[0]?.length || 1;
                const cellW = w / columns;
                const cellH = h / bins;
                for (let column = 0; column < columns; column += 1) {
                    const frame = frames[column];
                    const px = x + column * cellW;
                    for (let row = 0; row < frame.length; row += 1) {
                        const value = frame[row];
                        if (value < 4) continue;
                        const py = y + (bins - row - 1) * cellH;
                        ctx.fillStyle = heatColor(value);
                        ctx.fillRect(px, py, cellW + 0.25, cellH + 0.25);
                    }
                }
            }

            function drawEmpty(ctx, x, y, w, h) {
                ctx.fillStyle = '#777777';
                ctx.font = '13px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText('Waiting for audio', x + w / 2, y + h / 2);
            }

            function drawAxes(ctx, x, y, w, h) {
                const labels = [['16k', 0], ['4k', 0.23], ['1k', 0.46], ['250', 0.69], ['60', 0.91]];
                ctx.font = '11px sans-serif';
                ctx.textAlign = 'right';
                ctx.fillStyle = '#8a8a8a';
                ctx.strokeStyle = '#252525';
                for (const [label, position] of labels) {
                    const ly = y + h * position;
                    ctx.beginPath();
                    ctx.moveTo(x, ly);
                    ctx.lineTo(x + w, ly);
                    ctx.stroke();
                    ctx.fillText(label, x - 8, ly + 4);
                }

                const times = [['-30s', 0, 'center'], ['-20s', 0.333, 'center'], ['-10s', 0.667, 'center'], ['now', 1, 'right']];
                const axisY = y + h + 17;
                for (const [label, position, align] of times) {
                    ctx.textAlign = align;
                    ctx.fillText(label, x + w * position, axisY);
                }
            }

            function drawWaveform(ctx, waveform, x, y, w, h) {
                ctx.strokeStyle = '#2c2c2c';
                ctx.beginPath();
                ctx.moveTo(x, y + h / 2);
                ctx.lineTo(x + w, y + h / 2);
                ctx.stroke();

                if (!waveform.length) {
                    ctx.fillStyle = '#777777';
                    ctx.font = '11px sans-serif';
                    ctx.textAlign = 'left';
                    ctx.fillText('Waveform', x, y + 23);
                    return;
                }

                const barW = w / waveform.length;
                ctx.fillStyle = '#b78a42';
                for (let i = 0; i < waveform.length; i += 1) {
                    const value = waveform[i] / 255;
                    const height = Math.max(1, value * h);
                    ctx.fillRect(x + i * barW, y + h / 2 - height / 2, Math.max(1, barW - 0.6), height);
                }
            }

            function drawBeatPanel(ctx, payload, x, y, w) {
                const beatPos = (payload.beatPosition || 0) / 255;
                const tempo = payload.tempo || 0;
                const centerX = x + w / 2;
                const centerY = y + 74;
                const radius = 43;

                ctx.fillStyle = '#d4d4d4';
                ctx.font = '600 13px sans-serif';
                ctx.textAlign = 'left';
                ctx.fillText('Beat', x + 12, y + 22);
                ctx.fillStyle = '#a8a8a8';
                ctx.font = '12px sans-serif';
                ctx.textAlign = 'right';
                ctx.fillText(`${tempo} BPM`, x + w - 12, y + 22);

                ctx.fillStyle = '#101010';
                ctx.strokeStyle = '#303030';
                ctx.beginPath();
                ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
                ctx.fill();
                ctx.stroke();

                const pulseRadius = radius * (0.55 + (1 - beatPos) * 0.35);
                const opacity = 0.22 + (1 - beatPos) * 0.34;
                ctx.fillStyle = `rgba(183,138,66,${opacity.toFixed(2)})`;
                ctx.beginPath();
                ctx.arc(centerX, centerY, pulseRadius, 0, Math.PI * 2);
                ctx.fill();

                const arcRadius = radius + 7;
                const end = -Math.PI / 2 + Math.max(0.01, beatPos * Math.PI * 2);
                ctx.strokeStyle = '#b78a42';
                ctx.lineWidth = 3;
                ctx.lineCap = 'round';
                ctx.beginPath();
                ctx.arc(centerX, centerY, arcRadius, -Math.PI / 2, end);
                ctx.stroke();
                ctx.lineWidth = 1;

                ctx.fillStyle = '#f1f1f1';
                ctx.font = '600 20px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText(String(tempo), centerX, centerY + 5);
                ctx.fillStyle = '#8a8a8a';
                ctx.font = '10px sans-serif';
                ctx.fillText('BPM', centerX, centerY + 24);

                let barY = y + 140;
                drawPanelBar(ctx, x + 12, barY, w - 24, 'Energy', payload.energy, '#a98242');
                drawPanelBar(ctx, x + 12, barY + 28, w - 24, 'Bass', payload.bass, '#9f5d38');
                drawPanelBar(ctx, x + 12, barY + 56, w - 24, 'Mid', payload.mid, '#8f8846');
                drawPanelBar(ctx, x + 12, barY + 84, w - 24, 'High', payload.high, '#b0a15c');
            }

            function drawPanelBar(ctx, x, y, width, label, rawValue, color) {
                const value = (rawValue || 0) / 255;
                ctx.fillStyle = '#a8a8a8';
                ctx.font = '11px sans-serif';
                ctx.textAlign = 'left';
                ctx.fillText(label, x, y);
                ctx.textAlign = 'right';
                ctx.fillText(`${Math.round(value * 100)}%`, x + width, y);
                ctx.fillStyle = '#242424';
                ctx.fillRect(x, y + 13, width, 7);
                ctx.fillStyle = color;
                ctx.fillRect(x, y + 13, width * value, 7);
            }

            function heatColor(rawValue) {
                const value = Math.max(0, Math.min(1, rawValue / 255));
                if (value < 0.35) return mix('#181818', '#4a4030', value / 0.35);
                if (value < 0.72) return mix('#4a4030', '#b77633', (value - 0.35) / 0.37);
                return mix('#b77633', '#f4dc73', (value - 0.72) / 0.28);
            }

            function mix(start, end, amount) {
                const a = hex(start);
                const b = hex(end);
                const r = Math.round(a[0] + (b[0] - a[0]) * amount);
                const g = Math.round(a[1] + (b[1] - a[1]) * amount);
                const bl = Math.round(a[2] + (b[2] - a[2]) * amount);
                return `rgb(${r},${g},${bl})`;
            }

            function hex(value) {
                const clean = value.replace('#', '');
                return [
                    parseInt(clean.slice(0, 2), 16),
                    parseInt(clean.slice(2, 4), 16),
                    parseInt(clean.slice(4, 6), 16),
                ];
            }
            </script>
        '''
