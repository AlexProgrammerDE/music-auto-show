import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react"
import {
  ACESFilmicToneMapping,
  AdditiveBlending,
  BoxGeometry,
  Color,
  ConeGeometry,
  DirectionalLight,
  DoubleSide,
  GridHelper,
  Group,
  HemisphereLight,
  LineBasicMaterial,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  PerspectiveCamera,
  PlaneGeometry,
  Scene,
  SphereGeometry,
  SRGBColorSpace,
  TorusGeometry,
  Vector3,
  WebGLRenderer,
} from "three"

import type {
  FixtureConfig,
  FixtureProfile,
  FixtureState,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import {
  effectBeamTarget,
  fixtureBrightness,
  fixtureColor,
  movingBeamTarget,
  type StagePoint,
} from "@/lib/stage-view-model"

type FixtureVisual = {
  readonly fixture: FixtureConfig
  readonly profile: FixtureProfile | undefined
  readonly position: Vector3
  readonly housingMaterial: MeshStandardMaterial
  readonly lensMaterial: MeshStandardMaterial
  readonly beams: readonly Mesh<ConeGeometry, MeshBasicMaterial>[]
  readonly strobeRing: Mesh<TorusGeometry, MeshBasicMaterial>
}

type StageRuntime = {
  readonly renderer: WebGLRenderer
  readonly scene: Scene
  readonly camera: PerspectiveCamera
  readonly fixtures: ReadonlyMap<string, FixtureVisual>
  readonly floorMaterial: MeshStandardMaterial
  readonly trussMaterial: MeshStandardMaterial
  readonly grid: GridHelper
}

const UP = new Vector3(0, 1, 0)
const OFF_COLOR = new Color(0.08, 0.09, 0.09)

function stageTheme() {
  const dark = document.documentElement.classList.contains("dark")
  return dark
    ? { background: 0x090c0d, floor: 0x171d1f, structure: 0x7f898c, grid: 0x30383a }
    : { background: 0xf8f9f9, floor: 0xe9eded, structure: 0x687174, grid: 0xc8d0d2 }
}

function applyTheme(runtime: StageRuntime) {
  const theme = stageTheme()
  runtime.renderer.setClearColor(theme.background, 1)
  runtime.scene.background = new Color(theme.background)
  runtime.floorMaterial.color.setHex(theme.floor)
  runtime.trussMaterial.color.setHex(theme.structure)
  runtime.fixtures.forEach((fixture) => fixture.housingMaterial.color.setHex(theme.structure))
  const materials = Array.isArray(runtime.grid.material)
    ? runtime.grid.material
    : [runtime.grid.material]
  materials.forEach((material) => {
    if (material instanceof LineBasicMaterial) material.color.setHex(theme.grid)
  })
}

function setBeamTransform(
  beam: Mesh<ConeGeometry, MeshBasicMaterial>,
  origin: Vector3,
  target: StagePoint,
) {
  const targetVector = new Vector3(target.x, target.y, target.z)
  const length = origin.distanceTo(targetVector)
  const towardSource = origin.clone().sub(targetVector).normalize()
  beam.position.lerpVectors(origin, targetVector, 0.5)
  beam.quaternion.setFromUnitVectors(UP, towardSource)
  beam.scale.set(0.52, length, 0.52)
}

function disposeScene(runtime: StageRuntime) {
  runtime.scene.traverse((object) => {
    if (!(object instanceof Mesh)) return
    object.geometry.dispose()
    const materials = Array.isArray(object.material) ? object.material : [object.material]
    materials.forEach((material) => material.dispose())
  })
  runtime.grid.geometry.dispose()
  const gridMaterials = Array.isArray(runtime.grid.material)
    ? runtime.grid.material
    : [runtime.grid.material]
  gridMaterials.forEach((material) => material.dispose())
  runtime.renderer.dispose()
}

function createStageRuntime(
  canvas: HTMLCanvasElement,
  fixtures: readonly FixtureConfig[],
  profiles: readonly FixtureProfile[],
) {
  const renderer = new WebGLRenderer({
    canvas,
    antialias: true,
    powerPreference: "high-performance",
  })
  renderer.outputColorSpace = SRGBColorSpace
  renderer.toneMapping = ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.05

  const scene = new Scene()
  const camera = new PerspectiveCamera(42, 1, 0.1, 40)
  camera.position.set(0, 4.9, 8.2)
  camera.lookAt(0, 1.35, 0)
  scene.add(new HemisphereLight(0xffffff, 0x1b2224, 1.7))
  const keyLight = new DirectionalLight(0xffffff, 1.4)
  keyLight.position.set(3, 6, 5)
  scene.add(keyLight)

  const floorMaterial = new MeshStandardMaterial({ roughness: 0.9, metalness: 0.05 })
  const floor = new Mesh(new PlaneGeometry(11, 9), floorMaterial)
  floor.rotation.x = -Math.PI / 2
  floor.position.z = 0.8
  scene.add(floor)
  const grid = new GridHelper(10, 10)
  grid.position.set(0, 0.015, 0.8)
  scene.add(grid)

  const trussMaterial = new MeshStandardMaterial({ roughness: 0.42, metalness: 0.8 })
  const truss = new Group()
  const trussWidth = 7.4
  const crossbar = new Mesh(new BoxGeometry(trussWidth, 0.1, 0.1), trussMaterial)
  crossbar.position.y = 3.45
  truss.add(crossbar)
  for (const x of [-trussWidth / 2, trussWidth / 2]) {
    const upright = new Mesh(new BoxGeometry(0.1, 3.45, 0.1), trussMaterial)
    upright.position.set(x, 1.72, 0)
    truss.add(upright)
  }
  scene.add(truss)

  const profileByName = new Map(profiles.map((profile) => [profile.name, profile]))
  const fixtureVisuals = new Map<string, FixtureVisual>()
  fixtures.forEach((fixture, index) => {
    const x = fixtures.length === 1 ? 0 : -3 + (index / (fixtures.length - 1)) * 6
    const position = new Vector3(x, 3.22, 0)
    const housingMaterial = new MeshStandardMaterial({ roughness: 0.5, metalness: 0.65 })
    const housing = new Mesh(new BoxGeometry(0.48, 0.24, 0.4), housingMaterial)
    housing.position.copy(position)
    scene.add(housing)

    const lensMaterial = new MeshStandardMaterial({
      color: OFF_COLOR,
      emissive: OFF_COLOR,
      emissiveIntensity: 0,
      roughness: 0.2,
    })
    const lens = new Mesh(new SphereGeometry(0.105, 20, 12), lensMaterial)
    lens.position.copy(position).add(new Vector3(0, -0.15, 0.07))
    scene.add(lens)

    const profile = profileByName.get(fixture.profileName)
    const effectFixture = profile?.fixtureType.toLowerCase() === "effect"
    const beams = Array.from({ length: effectFixture ? 4 : 1 }, () => {
      const material = new MeshBasicMaterial({
        color: OFF_COLOR,
        transparent: true,
        opacity: 0,
        depthWrite: false,
        side: DoubleSide,
        blending: AdditiveBlending,
      })
      const beam = new Mesh(new ConeGeometry(1, 1, 20, 1, true), material)
      beam.visible = false
      scene.add(beam)
      return beam
    })
    const strobeRing = new Mesh(
      new TorusGeometry(0.17, 0.018, 8, 28),
      new MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.85 }),
    )
    strobeRing.position.copy(lens.position)
    strobeRing.rotation.x = Math.PI / 2
    strobeRing.visible = false
    scene.add(strobeRing)

    fixtureVisuals.set(fixture.id || fixture.name, {
      fixture,
      profile,
      position: lens.position.clone(),
      housingMaterial,
      lensMaterial,
      beams,
      strobeRing,
    })
  })

  const runtime: StageRuntime = {
    renderer,
    scene,
    camera,
    fixtures: fixtureVisuals,
    floorMaterial,
    trussMaterial,
    grid,
  }
  applyTheme(runtime)
  return runtime
}

function updateFixtureVisuals(runtime: StageRuntime, states: readonly FixtureState[]) {
  const stateById = new Map(states.map((state) => [state.fixtureId, state]))
  runtime.fixtures.forEach((visual, fixtureId) => {
    const state = stateById.get(fixtureId)
    const color = state ? fixtureColor(state) : { red: 0, green: 0, blue: 0 }
    const brightness = state ? fixtureBrightness(state) : 0
    const threeColor = new Color(color.red / 255, color.green / 255, color.blue / 255)
    visual.lensMaterial.color.copy(brightness > 0 ? threeColor : OFF_COLOR)
    visual.lensMaterial.emissive.copy(brightness > 0 ? threeColor : OFF_COLOR)
    visual.lensMaterial.emissiveIntensity = brightness * 3.2
    visual.strobeRing.visible = Boolean(state && state.strobe > 0 && brightness > 0.02)
    visual.strobeRing.material.color.copy(threeColor)

    const effectFixture = visual.profile?.fixtureType.toLowerCase() === "effect"
    visual.beams.forEach((beam, beamIndex) => {
      beam.visible = Boolean(state && brightness > 0.02)
      beam.material.color.copy(threeColor)
      beam.material.opacity =
        Math.min(0.34, 0.07 + brightness * 0.27) * (state && state.strobe > 0 ? 0.72 : 1)
      if (!state) return
      const target = effectFixture
        ? effectBeamTarget(visual.position.x, visual.position.z, state.effectRotation, beamIndex)
        : movingBeamTarget(
            visual.position.x,
            visual.position.z,
            visual.fixture,
            visual.profile,
            state,
          )
      setBeamTransform(beam, visual.position, target)
    })
  })
  runtime.renderer.render(runtime.scene, runtime.camera)
}

export function StageView({
  fixtures,
  profiles,
  states,
}: {
  readonly fixtures: readonly FixtureConfig[]
  readonly profiles: readonly FixtureProfile[]
  readonly states: readonly FixtureState[]
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const runtimeRef = useRef<StageRuntime | undefined>(undefined)
  const [webglUnavailable, setWebglUnavailable] = useState(false)
  const orderedFixtures = useMemo(
    () => fixtures.toSorted((left, right) => left.position - right.position),
    [fixtures],
  )
  const stateById = useMemo(
    () => new Map(states.map((state) => [state.fixtureId, state])),
    [states],
  )
  const renderStates = useEffectEvent((runtime: StageRuntime) => {
    updateFixtureVisuals(runtime, states)
  })

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    let runtime: StageRuntime
    try {
      runtime = createStageRuntime(canvas, orderedFixtures, profiles)
      runtimeRef.current = runtime
      setWebglUnavailable(false)
    } catch {
      setWebglUnavailable(true)
      return
    }

    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return
      const width = Math.max(1, Math.round(entry.contentRect.width))
      const height = Math.max(1, Math.round(entry.contentRect.height))
      runtime.renderer.setPixelRatio(Math.min(2, Math.max(1, window.devicePixelRatio || 1)))
      runtime.renderer.setSize(width, height, false)
      runtime.camera.aspect = width / height
      runtime.camera.updateProjectionMatrix()
      runtime.renderer.render(runtime.scene, runtime.camera)
    })
    observer.observe(canvas)

    const themeObserver = new MutationObserver(() => {
      applyTheme(runtime)
      runtime.renderer.render(runtime.scene, runtime.camera)
    })
    themeObserver.observe(document.documentElement, { attributeFilter: ["class"] })
    const handleContextLost = (event: Event) => {
      event.preventDefault()
      setWebglUnavailable(true)
    }
    const handleContextRestored = () => {
      setWebglUnavailable(false)
      runtime.renderer.render(runtime.scene, runtime.camera)
    }
    canvas.addEventListener("webglcontextlost", handleContextLost)
    canvas.addEventListener("webglcontextrestored", handleContextRestored)
    renderStates(runtime)
    return () => {
      observer.disconnect()
      themeObserver.disconnect()
      canvas.removeEventListener("webglcontextlost", handleContextLost)
      canvas.removeEventListener("webglcontextrestored", handleContextRestored)
      runtimeRef.current = undefined
      disposeScene(runtime)
    }
  }, [orderedFixtures, profiles])

  useEffect(() => {
    const runtime = runtimeRef.current
    if (runtime) renderStates(runtime)
  }, [states])

  return (
    <div className="relative h-80 overflow-hidden bg-background">
      <canvas
        ref={canvasRef}
        className="block size-full touch-manipulation"
        aria-label="Live 3D stage preview showing fixture position, movement, color, intensity, strobe state, and effect beams"
      >
        Live 3D stage preview
      </canvas>
      <div className="pointer-events-none absolute top-[13%] right-[18%] left-[18%] flex justify-around gap-2">
        {orderedFixtures.map((fixture) => {
          const state = stateById.get(fixture.id)
          return (
            <span
              key={fixture.id}
              className="min-w-0 text-center font-heading text-[10px] text-muted-foreground"
            >
              <span className="block truncate">{fixture.name}</span>
              {state && state.strobe > 0 ? (
                <span className="mt-0.5 block text-[9px] text-foreground">Strobe</span>
              ) : null}
            </span>
          )
        })}
      </div>
      {orderedFixtures.length === 0 ? (
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">
          Add fixtures to preview the stage
        </p>
      ) : null}
      {webglUnavailable ? (
        <p className="absolute inset-0 flex items-center justify-center bg-background px-6 text-center text-xs text-muted-foreground">
          The 3D stage preview needs WebGL. Live fixture values remain available below.
        </p>
      ) : null}
      <ul className="sr-only">
        {orderedFixtures.map((fixture) => {
          const state = stateById.get(fixture.id)
          return (
            <li key={fixture.id}>
              {fixture.name}: dimmer {state?.dimmer ?? 0}, pan {state?.pan ?? 0}, tilt{" "}
              {state?.tilt ?? 0}, strobe {state?.strobe ?? 0}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
