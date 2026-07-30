import type {
  FixtureState,
  LiveAudioFrame,
  LiveFrame,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"

const FRAME_RETENTION = 8
export const LIVE_HISTORY_DURATION_MS = 5_000
export const LIVE_RENDER_DELAY_MS = 30

export interface TimedLiveFrame {
  readonly capturedAt: number
  readonly frame: LiveFrame
}

export interface LiveFrameSample {
  readonly alpha: number
  readonly current: TimedLiveFrame
  readonly previous: TimedLiveFrame
}

export interface ProjectedBeat {
  readonly barPosition: number
  readonly beatIndex: number
  readonly beatPosition: number
  readonly estimatedBar: number
  readonly estimatedBeat: number
  readonly meter: number
}

export interface LiveAnalysisSample {
  readonly bass: number
  readonly beatActivation: number
  readonly capturedAt: number
  readonly downbeatActivation: number
  readonly energy: number
  readonly high: number
  readonly mid: number
}

export interface LiveSpectrogramFrame {
  readonly bins: readonly number[]
  readonly capturedAt: number
}

type Listener = (frame: TimedLiveFrame) => void
type StoreListener = () => void

let frames: TimedLiveFrame[] = []
let analysisHistory: LiveAnalysisSample[] = []
let spectrogramFrames: LiveSpectrogramFrame[] = []
let minimumClockOffset = Number.POSITIVE_INFINITY
const listeners = new Set<Listener>()
const storeListeners = new Set<StoreListener>()

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value))
}

export function publishLiveFrame(
  frame: LiveFrame,
  receivedAt = performance.now(),
  timeOrigin = performance.timeOrigin,
) {
  const serverCapturedAt = Number(frame.capturedAtUnixMs)
  const observedClockOffset = timeOrigin + receivedAt - serverCapturedAt
  if (
    !Number.isFinite(minimumClockOffset) ||
    Math.abs(observedClockOffset - minimumClockOffset) > 60_000
  ) {
    minimumClockOffset = observedClockOffset
  } else {
    minimumClockOffset = Math.min(minimumClockOffset, observedClockOffset)
  }
  const transportJitter = Math.max(0, observedClockOffset - minimumClockOffset)
  const timed = {
    capturedAt: receivedAt - transportJitter,
    frame,
  } satisfies TimedLiveFrame
  const latest = frames.at(-1)
  if (latest && frame.sequence <= latest.frame.sequence) {
    frames = []
    analysisHistory = []
    spectrogramFrames = []
  }
  frames.push(timed)
  if (frames.length > FRAME_RETENTION) frames.shift()
  const audio = frame.audio
  if (audio) {
    analysisHistory.push({
      bass: audio.bass,
      beatActivation: audio.beatActivation,
      capturedAt: timed.capturedAt,
      downbeatActivation: audio.downbeatActivation,
      energy: audio.energy,
      high: audio.high,
      mid: audio.mid,
    })
    if (audio.spectrogramBins.length > 0) {
      spectrogramFrames.push({
        bins: audio.spectrogramBins,
        capturedAt: timed.capturedAt,
      })
    }
    const cutoff = timed.capturedAt - LIVE_HISTORY_DURATION_MS
    while (analysisHistory.length > 1 && (analysisHistory[0]?.capturedAt ?? cutoff) < cutoff) {
      analysisHistory.shift()
    }
    while (spectrogramFrames.length > 1 && (spectrogramFrames[0]?.capturedAt ?? cutoff) < cutoff) {
      spectrogramFrames.shift()
    }
  }
  for (const listener of listeners) listener(timed)
  for (const listener of storeListeners) listener()
}

export function subscribeLiveFrame(listener: Listener) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function subscribeLiveFrameStore(listener: StoreListener) {
  storeListeners.add(listener)
  return () => {
    storeListeners.delete(listener)
  }
}

export function latestLiveFrame() {
  return frames.at(-1)
}

export function latestLiveAudio() {
  return latestLiveFrame()?.frame.audio
}

export function liveAnalysisHistory() {
  return analysisHistory as readonly LiveAnalysisSample[]
}

export function liveSpectrogramFrames() {
  return spectrogramFrames as readonly LiveSpectrogramFrame[]
}

export function sampleLiveFrame(
  now: number,
  renderDelay = LIVE_RENDER_DELAY_MS,
): LiveFrameSample | undefined {
  if (frames.length === 0) return undefined
  const target = now - renderDelay
  const currentIndex = frames.findIndex((frame) => frame.capturedAt >= target)
  if (currentIndex === -1) {
    const frame = frames.at(-1)
    return frame ? { alpha: 1, current: frame, previous: frame } : undefined
  }
  if (currentIndex <= 0) {
    const frame = frames[Math.max(0, currentIndex)]
    return frame ? { alpha: 1, current: frame, previous: frame } : undefined
  }
  const previous = frames[currentIndex - 1]
  const current = frames[currentIndex]
  if (!previous || !current) return undefined
  const duration = Math.max(1, current.capturedAt - previous.capturedAt)
  return {
    alpha: clamp01((target - previous.capturedAt) / duration),
    current,
    previous,
  }
}

export function interpolateNumber(previous: number, current: number, alpha: number) {
  return previous + (current - previous) * clamp01(alpha)
}

export function interpolatePhase(previous: number, current: number, alpha: number) {
  let delta = current - previous
  if (delta > 0.5) delta -= 1
  if (delta < -0.5) delta += 1
  return (previous + delta * clamp01(alpha) + 1) % 1
}

function interpolateDmx(previous: number, current: number, alpha: number) {
  return Math.round(interpolateNumber(previous, current, alpha))
}

function interpolateFineAxis(
  previousCoarse: number,
  previousFine: number,
  currentCoarse: number,
  currentFine: number,
  alpha: number,
) {
  const value = interpolateDmx(
    previousCoarse * 256 + previousFine,
    currentCoarse * 256 + currentFine,
    alpha,
  )
  return {
    coarse: Math.floor(value / 256),
    fine: value % 256,
  }
}

export function interpolateFixtureState(
  previous: FixtureState,
  current: FixtureState,
  alpha: number,
): FixtureState {
  const pan = interpolateFineAxis(
    previous.pan,
    previous.panFine,
    current.pan,
    current.panFine,
    alpha,
  )
  const tilt = interpolateFineAxis(
    previous.tilt,
    previous.tiltFine,
    current.tilt,
    current.tiltFine,
    alpha,
  )
  const discrete = alpha < 0.5 ? previous : current
  return {
    ...discrete,
    fixtureId: current.fixtureId,
    fixtureName: current.fixtureName,
    red: interpolateDmx(previous.red, current.red, alpha),
    green: interpolateDmx(previous.green, current.green, alpha),
    blue: interpolateDmx(previous.blue, current.blue, alpha),
    white: interpolateDmx(previous.white, current.white, alpha),
    amber: interpolateDmx(previous.amber, current.amber, alpha),
    uv: interpolateDmx(previous.uv, current.uv, alpha),
    cyan: interpolateDmx(previous.cyan, current.cyan, alpha),
    magenta: interpolateDmx(previous.magenta, current.magenta, alpha),
    yellow: interpolateDmx(previous.yellow, current.yellow, alpha),
    dimmer: interpolateDmx(previous.dimmer, current.dimmer, alpha),
    strobe: interpolateDmx(previous.strobe, current.strobe, alpha),
    pan: pan.coarse,
    panFine: pan.fine,
    tilt: tilt.coarse,
    tiltFine: tilt.fine,
    panTiltSpeed: interpolateDmx(previous.panTiltSpeed, current.panTiltSpeed, alpha),
    effectSpeed: interpolateDmx(previous.effectSpeed, current.effectSpeed, alpha),
    zoom: interpolateDmx(previous.zoom, current.zoom, alpha),
    focus: interpolateDmx(previous.focus, current.focus, alpha),
    iris: interpolateDmx(previous.iris, current.iris, alpha),
    effectRotation: interpolatePhase(previous.effectRotation, current.effectRotation, alpha),
  }
}

export function interpolatedFixtureStates(
  sample: LiveFrameSample | undefined,
  fallback: readonly FixtureState[],
) {
  const previous = sample?.previous.frame.fixtureStates
  const current = sample?.current.frame.fixtureStates
  if (!sample || !previous || !current || current.length === 0) return fallback
  const previousById = new Map(previous.map((state) => [state.fixtureId, state]))
  return current.map((state) => {
    const prior = previousById.get(state.fixtureId)
    return prior ? interpolateFixtureState(prior, state, sample.alpha) : state
  })
}

export function projectBeat(audio: LiveAudioFrame, capturedAt: number, now: number): ProjectedBeat {
  const meter = audio.meter === 2 || audio.meter === 3 || audio.meter === 4 ? audio.meter : 4
  const elapsedBeats = (Math.max(0, now - capturedAt) * Math.max(0, audio.tempo)) / 60_000
  const totalBeat = audio.beatPosition + elapsedBeats
  const crossedBeats = Math.floor(totalBeat)
  const beatPosition = totalBeat - crossedBeats
  const beatIndex = ((Math.max(1, audio.beatIndex) - 1 + crossedBeats) % meter) + 1
  const estimatedBeat = Number(audio.estimatedBeat) + crossedBeats
  const barPosition = (audio.barPosition + elapsedBeats / meter) % 1
  const crossedBars = Math.floor(
    (Math.max(1, audio.beatIndex) - 1 + audio.beatPosition + elapsedBeats) / meter,
  )
  return {
    barPosition,
    beatIndex,
    beatPosition,
    estimatedBar: Number(audio.estimatedBar) + crossedBars,
    estimatedBeat,
    meter,
  }
}

export function followEnvelope(
  current: number,
  target: number,
  deltaMilliseconds: number,
  attackMilliseconds = 28,
  releaseMilliseconds = 130,
) {
  const timeConstant = target > current ? attackMilliseconds : releaseMilliseconds
  const blend = 1 - Math.exp(-Math.max(0, deltaMilliseconds) / Math.max(1, timeConstant))
  return interpolateNumber(current, target, blend)
}

export function resetLiveFrameStore() {
  frames = []
  analysisHistory = []
  spectrogramFrames = []
  minimumClockOffset = Number.POSITIVE_INFINITY
  for (const listener of storeListeners) listener()
}
