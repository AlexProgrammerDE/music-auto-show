import { useEffect, useMemo, useRef, useState } from "react"
import {
  ACESFilmicToneMapping,
  AdditiveBlending,
  BoxGeometry,
  BufferGeometry,
  CanvasTexture,
  Color,
  ConeGeometry,
  DirectionalLight,
  DoubleSide,
  Float32BufferAttribute,
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
  Sprite,
  SpriteMaterial,
  SRGBColorSpace,
  Vector3,
  WebGLRenderer,
} from "three"

import {
  FixtureEmitterKind,
  FixtureVisualKind,
  type FixtureConfig,
  type FixtureEmitter,
  type FixtureState,
  type GrandMa2FixtureType,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import {
  beamTargetFromDirection,
  fixtureBrightness,
  fixtureColor,
  physicalAxisValue,
  type StagePoint,
} from "@/lib/stage-view-model"

type EmitterVisual = {
  readonly metadata: FixtureEmitter
  readonly lens: Mesh<SphereGeometry, MeshStandardMaterial>
  readonly beam: Mesh<ConeGeometry, MeshBasicMaterial>
  readonly localDirection: Vector3
}

type FixtureLabelVisual = {
  readonly canvas: HTMLCanvasElement
  readonly name: string
  readonly sprite: Sprite
  readonly texture: CanvasTexture
}

type FixtureVisual = {
  readonly fixture: FixtureConfig
  readonly fixtureType: GrandMa2FixtureType
  readonly root: Group
  readonly mount: Vector3
  readonly panPivot?: Group
  readonly tiltPivot?: Group
  readonly emitterGroup: Group
  readonly housingMaterials: readonly MeshStandardMaterial[]
  readonly emitters: readonly EmitterVisual[]
  readonly label?: FixtureLabelVisual
  rotationPhase: number
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
const OPTICAL_FORWARD = new Vector3(0, 0, 1)
const OFF_COLOR = new Color(0.055, 0.065, 0.07)
const FIXTURE_SCALE = 2.4
const FIXED_FIXTURE_PITCH_RADIANS = (58 * Math.PI) / 180
const FIXTURE_LABEL_FONT = '600 32px "Public Sans Variable", "Public Sans", sans-serif'
const FIXTURE_LABEL_HEIGHT = 52
const FIXTURE_LABEL_MAX_TEXT_WIDTH = 400
const FIXTURE_LABEL_WORLD_HEIGHT = 0.3

function stageTheme() {
  const dark = document.documentElement.classList.contains("dark")
  return dark
    ? {
        background: 0x090c0d,
        floor: 0x171d1f,
        structure: 0x7f898c,
        grid: 0x30383a,
        labelBackground: "rgba(9, 12, 13, 0.88)",
        labelBorder: "rgba(127, 137, 140, 0.42)",
        labelText: "#d9dfe0",
      }
    : {
        background: 0xf8f9f9,
        floor: 0xe9eded,
        structure: 0x687174,
        grid: 0xc8d0d2,
        labelBackground: "rgba(248, 249, 249, 0.9)",
        labelBorder: "rgba(104, 113, 116, 0.36)",
        labelText: "#252b2d",
      }
}

function fitFixtureLabel(context: CanvasRenderingContext2D, name: string, maxWidth: number) {
  const label = name.trim() || "Unnamed fixture"
  if (context.measureText(label).width <= maxWidth) return label

  const suffix = "…"
  let end = label.length
  while (end > 1 && context.measureText(`${label.slice(0, end)}${suffix}`).width > maxWidth) {
    end -= 1
  }
  return `${label.slice(0, end).trimEnd()}${suffix}`
}

function paintFixtureLabel(label: FixtureLabelVisual) {
  const context = label.canvas.getContext("2d")
  if (!context) return

  const theme = stageTheme()
  context.clearRect(0, 0, label.canvas.width, label.canvas.height)
  context.fillStyle = theme.labelBackground
  context.fillRect(0, 0, label.canvas.width, label.canvas.height)
  context.strokeStyle = theme.labelBorder
  context.lineWidth = 2
  context.strokeRect(1, 1, label.canvas.width - 2, label.canvas.height - 2)
  context.font = FIXTURE_LABEL_FONT
  context.fillStyle = theme.labelText
  context.textAlign = "center"
  context.textBaseline = "middle"
  context.fillText(label.name, label.canvas.width / 2, label.canvas.height / 2 + 1)
  label.texture.needsUpdate = true
}

function createFixtureLabel(name: string) {
  const canvas = document.createElement("canvas")
  const context = canvas.getContext("2d")
  if (!context) return undefined

  context.font = FIXTURE_LABEL_FONT
  const fittedName = fitFixtureLabel(context, name, FIXTURE_LABEL_MAX_TEXT_WIDTH)
  canvas.width = Math.ceil(context.measureText(fittedName).width + 34)
  canvas.height = FIXTURE_LABEL_HEIGHT

  const texture = new CanvasTexture(canvas)
  texture.colorSpace = SRGBColorSpace
  const sprite = new Sprite(
    new SpriteMaterial({
      map: texture,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    }),
  )
  sprite.center.set(0.5, 0)
  sprite.position.set(0, 0.24, 0)
  sprite.scale.set(
    Math.min(1.55, (canvas.width / canvas.height) * FIXTURE_LABEL_WORLD_HEIGHT),
    FIXTURE_LABEL_WORLD_HEIGHT,
    1,
  )
  sprite.renderOrder = 20

  const label = { canvas, name: fittedName, sprite, texture } satisfies FixtureLabelVisual
  paintFixtureLabel(label)
  return label
}

function applyTheme(runtime: StageRuntime) {
  const theme = stageTheme()
  runtime.renderer.setClearColor(theme.background, 1)
  runtime.scene.background = new Color(theme.background)
  runtime.floorMaterial.color.setHex(theme.floor)
  runtime.trussMaterial.color.setHex(theme.structure)
  runtime.fixtures.forEach((fixture) => {
    fixture.housingMaterials.forEach((material) => material.color.setHex(theme.structure))
    if (fixture.label) paintFixtureLabel(fixture.label)
  })
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
  const length = Math.max(0.05, origin.distanceTo(targetVector))
  const towardSource = origin.clone().sub(targetVector).normalize()
  beam.position.lerpVectors(origin, targetVector, 0.5)
  beam.quaternion.setFromUnitVectors(UP, towardSource)
  beam.scale.set(1, length, 1)
}

function disposeScene(runtime: StageRuntime) {
  runtime.scene.traverse((object) => {
    if (object instanceof Mesh) {
      object.geometry.dispose()
      const materials = Array.isArray(object.material) ? object.material : [object.material]
      materials.forEach((material) => material.dispose())
      return
    }
    if (object instanceof Sprite) {
      object.material.map?.dispose()
      object.material.dispose()
    }
  })
  runtime.grid.geometry.dispose()
  const gridMaterials = Array.isArray(runtime.grid.material)
    ? runtime.grid.material
    : [runtime.grid.material]
  gridMaterials.forEach((material) => material.dispose())
  runtime.renderer.dispose()
}

function fixtureDimensions(fixtureType: GrandMa2FixtureType) {
  const visual = fixtureType.visual
  return {
    width: Math.max(0.12, visual?.widthM ?? 0.24) * FIXTURE_SCALE,
    height: Math.max(0.1, visual?.heightM ?? 0.18) * FIXTURE_SCALE,
    depth: Math.max(0.1, visual?.depthM ?? 0.2) * FIXTURE_SCALE,
  }
}

function createHousingMaterial(color?: number) {
  return new MeshStandardMaterial({
    color: color && color > 0 ? color : 0x70797c,
    roughness: 0.48,
    metalness: 0.62,
  })
}

function createEmbeddedMeshes(fixtureType: GrandMa2FixtureType, root: Group) {
  const materials: MeshStandardMaterial[] = []
  for (const metadata of fixtureType.visual?.meshes ?? []) {
    if (metadata.vertices.length < 9 || metadata.indices.length < 3) continue
    const geometry = new BufferGeometry()
    geometry.setAttribute("position", new Float32BufferAttribute(metadata.vertices, 3))
    if (metadata.normals.length === metadata.vertices.length) {
      geometry.setAttribute("normal", new Float32BufferAttribute(metadata.normals, 3))
    } else {
      geometry.computeVertexNormals()
    }
    geometry.setIndex(metadata.indices)
    geometry.computeBoundingSphere()
    const material = createHousingMaterial(metadata.colorRgb)
    const mesh = new Mesh(geometry, material)
    mesh.scale.setScalar(FIXTURE_SCALE)
    root.add(mesh)
    materials.push(material)
  }
  return materials
}

function createEmitterVisual(
  metadata: FixtureEmitter,
  parent: Group,
  scene: Scene,
  localDirection = OPTICAL_FORWARD,
) {
  const lensMaterial = new MeshStandardMaterial({
    color: OFF_COLOR,
    emissive: OFF_COLOR,
    emissiveIntensity: 0,
    roughness: 0.18,
  })
  const lens = new Mesh(new SphereGeometry(0.045, 18, 12), lensMaterial)
  lens.position.set(
    metadata.xM * FIXTURE_SCALE,
    metadata.yM * FIXTURE_SCALE,
    metadata.zM * FIXTURE_SCALE,
  )
  parent.add(lens)

  const halfAngle = (Math.max(1, Math.min(170, metadata.beamAngleDegrees)) * Math.PI) / 360
  const beamMaterial = new MeshBasicMaterial({
    color: OFF_COLOR,
    transparent: true,
    opacity: 0,
    depthWrite: false,
    side: DoubleSide,
    blending: AdditiveBlending,
  })
  const beam = new Mesh(new ConeGeometry(Math.tan(halfAngle), 1, 24, 1, true), beamMaterial)
  beam.visible = false
  scene.add(beam)
  return {
    metadata,
    lens,
    beam,
    localDirection: localDirection.clone().normalize(),
  } satisfies EmitterVisual
}

function effectEmitterDirection(
  metadata: FixtureEmitter,
  dimensions: ReturnType<typeof fixtureDimensions>,
) {
  const x = (metadata.xM * FIXTURE_SCALE) / Math.max(0.01, dimensions.width * 0.52 * 0.5)
  const y = (metadata.yM * FIXTURE_SCALE) / Math.max(0.01, dimensions.height * 0.52 * 0.5)
  return new Vector3(x * 0.55, y * 0.45, 1).normalize()
}

function createMovingHead(
  fixture: FixtureConfig,
  fixtureType: GrandMa2FixtureType,
  mount: Vector3,
  scene: Scene,
) {
  const dimensions = fixtureDimensions(fixtureType)
  const root = new Group()
  root.position.copy(mount)
  scene.add(root)

  const label = createFixtureLabel(fixture.name)
  if (label) root.add(label.sprite)

  const housingMaterials = createEmbeddedMeshes(fixtureType, root)
  const bodyMaterial = createHousingMaterial()
  housingMaterials.push(bodyMaterial)

  const baseHeight = dimensions.height * 0.24
  const base = new Mesh(
    new BoxGeometry(dimensions.width, baseHeight, dimensions.depth),
    bodyMaterial,
  )
  base.position.y = -baseHeight / 2
  root.add(base)

  const panPivot = new Group()
  panPivot.position.y = -baseHeight
  root.add(panPivot)

  const armHeight = dimensions.height * 0.54
  const armWidth = Math.max(0.045, dimensions.width * 0.12)
  for (const side of [-1, 1]) {
    const arm = new Mesh(new BoxGeometry(armWidth, armHeight, armWidth), bodyMaterial)
    arm.position.set(side * dimensions.width * 0.37, -armHeight / 2, 0)
    panPivot.add(arm)
  }

  const tiltPivot = new Group()
  tiltPivot.position.y = -armHeight * 0.6
  panPivot.add(tiltPivot)
  const head = new Mesh(
    new BoxGeometry(dimensions.width * 0.58, dimensions.height * 0.32, dimensions.depth * 0.68),
    bodyMaterial,
  )
  tiltPivot.add(head)

  const emitterGroup = new Group()
  tiltPivot.add(emitterGroup)
  const metadata = fixtureType.visual?.emitters[0] ?? {
    id: "estimated-emitter",
    name: "Estimated beam",
    kind: FixtureEmitterKind.COLOR,
    beamAngleDegrees: 25,
    beamIntensity: 1000,
    xM: 0,
    yM: 0,
    zM: 0,
    colorRgb: 0,
    $typeName: "music_auto_show.v1.FixtureEmitter" as const,
  }
  const emitters = [createEmitterVisual(metadata, emitterGroup, scene)]
  return {
    fixture,
    fixtureType,
    root,
    mount,
    panPivot,
    tiltPivot,
    emitterGroup,
    housingMaterials,
    emitters,
    label,
    rotationPhase: 0,
  } satisfies FixtureVisual
}

function createFixedFixture(
  fixture: FixtureConfig,
  fixtureType: GrandMa2FixtureType,
  mount: Vector3,
  scene: Scene,
) {
  const dimensions = fixtureDimensions(fixtureType)
  const root = new Group()
  root.position.copy(mount)
  scene.add(root)

  const label = createFixtureLabel(fixture.name)
  if (label) root.add(label.sprite)

  const aimGroup = new Group()
  aimGroup.rotation.x = FIXED_FIXTURE_PITCH_RADIANS
  root.add(aimGroup)

  const housingMaterials = createEmbeddedMeshes(fixtureType, aimGroup)
  if (housingMaterials.length === 0) {
    const bodyMaterial = createHousingMaterial()
    const housing = new Mesh(
      new BoxGeometry(dimensions.width, dimensions.height, dimensions.depth),
      bodyMaterial,
    )
    housing.position.y = -dimensions.height / 2
    aimGroup.add(housing)
    housingMaterials.push(bodyMaterial)
  }

  const emitterGroup = new Group()
  emitterGroup.position.y = -dimensions.height / 2
  aimGroup.add(emitterGroup)
  const emitters = (fixtureType.visual?.emitters ?? []).map((metadata) =>
    createEmitterVisual(
      metadata,
      emitterGroup,
      scene,
      fixtureType.visual?.kind === FixtureVisualKind.EFFECT
        ? effectEmitterDirection(metadata, dimensions)
        : OPTICAL_FORWARD,
    ),
  )
  return {
    fixture,
    fixtureType,
    root,
    mount,
    emitterGroup,
    housingMaterials,
    emitters,
    label,
    rotationPhase: 0,
  } satisfies FixtureVisual
}

function createStageRuntime(
  canvas: HTMLCanvasElement,
  fixtures: readonly FixtureConfig[],
  fixtureTypes: readonly GrandMa2FixtureType[],
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

  const typeById = new Map(fixtureTypes.map((fixtureType) => [fixtureType.id, fixtureType]))
  const fixtureVisuals = new Map<string, FixtureVisual>()
  fixtures.forEach((fixture, index) => {
    const fixtureType = typeById.get(fixture.fixtureTypeId)
    if (!fixtureType?.visual) return
    const x = fixtures.length === 1 ? 0 : -3 + (index / (fixtures.length - 1)) * 6
    const mount = new Vector3(x, 3.35, 0)
    const visual =
      fixtureType.visual.kind === FixtureVisualKind.MOVING_HEAD
        ? createMovingHead(fixture, fixtureType, mount, scene)
        : createFixedFixture(fixture, fixtureType, mount, scene)
    fixtureVisuals.set(fixture.id || fixture.name, visual)
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

function wrappedPhaseStep(current: number, target: number) {
  let delta = target - current
  if (delta > 0.5) delta -= 1
  if (delta < -0.5) delta += 1
  return (current + delta * 0.16 + 1) % 1
}

function updateFixtureVisuals(
  runtime: StageRuntime,
  states: readonly FixtureState[],
  elapsedSeconds: number,
) {
  const stateById = new Map(states.map((state) => [state.fixtureId, state]))
  runtime.fixtures.forEach((visual, fixtureId) => {
    const state = stateById.get(fixtureId)
    const color = state ? fixtureColor(state) : { red: 0, green: 0, blue: 0 }
    const brightness = state ? fixtureBrightness(state) : 0
    const threeColor = new Color(color.red / 255, color.green / 255, color.blue / 255)
    const strobeRate = state ? 2 + (state.strobe / 255) * 23 : 0
    const strobePulse = !state || state.strobe === 0 || (elapsedSeconds * strobeRate) % 1 < 0.48

    if (state && visual.panPivot && visual.tiltPivot && visual.fixtureType.visual) {
      const metadata = visual.fixtureType.visual
      visual.panPivot.rotation.y =
        (physicalAxisValue(
          state.pan + state.panFine / 255,
          metadata.panMinDegrees,
          metadata.panMaxDegrees,
        ) *
          Math.PI) /
        180
      const tiltRadians =
        (physicalAxisValue(
          state.tilt + state.tiltFine / 255,
          metadata.tiltMinDegrees,
          metadata.tiltMaxDegrees,
        ) *
          Math.PI) /
        180
      visual.tiltPivot.rotation.x = Math.PI / 2 - tiltRadians
    }
    if (state) {
      visual.rotationPhase = wrappedPhaseStep(visual.rotationPhase, state.effectRotation)
    }
    visual.root.updateWorldMatrix(true, true)

    visual.emitters.forEach((emitter) => {
      const kind = emitter.metadata.kind
      const strobeEmitter = kind === FixtureEmitterKind.STROBE
      const emitterColor =
        strobeEmitter || kind === FixtureEmitterKind.WHITE ? new Color(1, 1, 1) : threeColor
      const emitterBrightness = strobeEmitter
        ? state && state.strobe > 0 && strobePulse
          ? Math.max(brightness, state.strobe / 255)
          : 0
        : strobePulse
          ? brightness
          : 0
      const photometricGain = Math.max(
        0.35,
        Math.min(1.5, Math.sqrt(Math.max(1, emitter.metadata.beamIntensity) / 1000)),
      )
      emitter.lens.material.color.copy(emitterBrightness > 0 ? emitterColor : OFF_COLOR)
      emitter.lens.material.emissive.copy(emitterBrightness > 0 ? emitterColor : OFF_COLOR)
      emitter.lens.material.emissiveIntensity = emitterBrightness * 3.5 * photometricGain
      emitter.beam.visible = Boolean(state && emitterBrightness > 0.02)
      emitter.beam.material.color.copy(emitterColor)
      emitter.beam.material.opacity = Math.min(
        0.34,
        (0.035 + emitterBrightness * 0.22) * photometricGain,
      )
      if (!state || !emitter.beam.visible || !visual.fixtureType.visual) return

      const origin = new Vector3()
      emitter.lens.getWorldPosition(origin)
      const localDirection = emitter.localDirection.clone()
      if (visual.fixtureType.visual.kind === FixtureVisualKind.EFFECT) {
        localDirection.applyAxisAngle(OPTICAL_FORWARD, visual.rotationPhase * Math.PI * 2)
      }
      const worldDirection = emitter.lens.localToWorld(localDirection).sub(origin).normalize()
      const target: StagePoint = beamTargetFromDirection(origin, worldDirection)
      setBeamTransform(emitter.beam, origin, target)
    })
  })
  runtime.renderer.render(runtime.scene, runtime.camera)
}

export function StageView({
  fixtures,
  fixtureTypes,
  states,
}: {
  readonly fixtures: readonly FixtureConfig[]
  readonly fixtureTypes: readonly GrandMa2FixtureType[]
  readonly states: readonly FixtureState[]
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const latestStatesRef = useRef(states)
  const [webglUnavailable, setWebglUnavailable] = useState(false)
  const orderedFixtures = useMemo(
    () => fixtures.toSorted((left, right) => left.position - right.position),
    [fixtures],
  )
  const stateById = useMemo(
    () => new Map(states.map((state) => [state.fixtureId, state])),
    [states],
  )

  useEffect(() => {
    latestStatesRef.current = states
  }, [states])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    let runtime: StageRuntime
    try {
      runtime = createStageRuntime(canvas, orderedFixtures, fixtureTypes)
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
    })
    observer.observe(canvas)

    const themeObserver = new MutationObserver(() => applyTheme(runtime))
    themeObserver.observe(document.documentElement, { attributeFilter: ["class"] })
    const handleContextLost = (event: Event) => {
      event.preventDefault()
      setWebglUnavailable(true)
    }
    const handleContextRestored = () => setWebglUnavailable(false)
    canvas.addEventListener("webglcontextlost", handleContextLost)
    canvas.addEventListener("webglcontextrestored", handleContextRestored)

    let animationFrame = 0
    const render = (time: number) => {
      updateFixtureVisuals(runtime, latestStatesRef.current, time / 1000)
      animationFrame = requestAnimationFrame(render)
    }
    animationFrame = requestAnimationFrame(render)

    return () => {
      cancelAnimationFrame(animationFrame)
      observer.disconnect()
      themeObserver.disconnect()
      canvas.removeEventListener("webglcontextlost", handleContextLost)
      canvas.removeEventListener("webglcontextrestored", handleContextRestored)
      disposeScene(runtime)
    }
  }, [orderedFixtures, fixtureTypes])

  return (
    <div className="relative h-80 overflow-hidden bg-background">
      <canvas
        ref={canvasRef}
        className="block size-full touch-manipulation"
        aria-label="Live 3D stage preview driven by grandMA2 body, emitter, beam, pan, and tilt metadata"
      >
        Live 3D stage preview
      </canvas>
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
