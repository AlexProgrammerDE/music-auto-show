import { useEffect, useId, useMemo, useRef, useState } from "react"
import {
  ACESFilmicToneMapping,
  AdditiveBlending,
  BoxGeometry,
  BufferGeometry,
  CanvasTexture,
  Color,
  CylinderGeometry,
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
  type Object3D,
  PerspectiveCamera,
  PlaneGeometry,
  Quaternion,
  Scene,
  SphereGeometry,
  Sprite,
  SpriteMaterial,
  SRGBColorSpace,
  Vector3,
  WebGLRenderer,
} from "three"
import { OrbitControls } from "three/addons/controls/OrbitControls.js"

import {
  FixtureEmitterKind,
  FixtureModelKind,
  FixtureModelNodeKind,
  FixtureVisualKind,
  type FixtureConfig,
  type FixtureEmitter,
  type FixtureModelNode,
  type FixtureState,
  type GrandMa2FixtureType,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { interpolatedFixtureStates, sampleLiveFrame } from "@/lib/live-frame-store"
import {
  beamAngleFromZoom,
  beamTargetFromDirection,
  fixtureBrightness,
  fixtureColor,
  physicalAxisValue,
  rotatedEffectDirection,
  strobePatternLevel,
  type StagePoint,
} from "@/lib/stage-view-model"

type EmitterVisual = {
  readonly metadata: FixtureEmitter
  readonly lens: Mesh<BufferGeometry, MeshStandardMaterial>
  readonly lenses: readonly Mesh<BufferGeometry, MeshStandardMaterial>[]
  readonly beam?: Mesh<BufferGeometry, MeshBasicMaterial>
  readonly beamOrigin: Object3D
  readonly beamClippingDistanceMeters: number
  readonly directionFrame: Object3D
  readonly localDirection: Vector3
  readonly apertureMeters: number
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
  readonly importedAxes: boolean
  readonly panBaseQuaternion?: Quaternion
  readonly tiltBaseQuaternion?: Quaternion
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
  readonly controls: OrbitControls
  readonly fixtures: ReadonlyMap<string, FixtureVisual>
  readonly floorMaterial: MeshStandardMaterial
  readonly trussMaterial: MeshStandardMaterial
  readonly grid: GridHelper
  reducedMotion: boolean
}

const UP = new Vector3(0, 1, 0)
const TILT_AXIS = new Vector3(1, 0, 0)
const OPTICAL_FORWARD = new Vector3(0, 0, 1)
const OFF_COLOR = new Color(0.055, 0.065, 0.07)
const FIXTURE_SCALE = 2.4
const FIXTURE_LABEL_FONT = '600 32px "Public Sans Variable", "Public Sans", sans-serif'
const FIXTURE_LABEL_HEIGHT = 52
const FIXTURE_LABEL_MAX_TEXT_WIDTH = 400
const FIXTURE_LABEL_WORLD_HEIGHT = 0.3
const DEGREES_TO_RADIANS = Math.PI / 180

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

function createBeamGeometry(segments = 24) {
  const positions: number[] = []
  const indices: number[] = []
  for (let ring = 0; ring < 2; ring += 1) {
    for (let segment = 0; segment < segments; segment += 1) {
      const angle = (segment / segments) * Math.PI * 2
      positions.push(Math.cos(angle), ring === 0 ? 0.5 : -0.5, Math.sin(angle))
    }
  }
  for (let segment = 0; segment < segments; segment += 1) {
    const next = (segment + 1) % segments
    const top = segment
    const topNext = next
    const bottom = segments + segment
    const bottomNext = segments + next
    indices.push(top, bottom, topNext, topNext, bottom, bottomNext)
  }
  const geometry = new BufferGeometry()
  geometry.setAttribute("position", new Float32BufferAttribute(positions, 3))
  geometry.setIndex(indices)
  return geometry
}

function updateBeamGeometry(
  geometry: BufferGeometry,
  length: number,
  sourceRadius: number,
  targetRadius: number,
) {
  const position = geometry.getAttribute("position")
  const segments = position.count / 2
  for (let ring = 0; ring < 2; ring += 1) {
    const radius = ring === 0 ? sourceRadius : targetRadius
    const y = ring === 0 ? length / 2 : -length / 2
    for (let segment = 0; segment < segments; segment += 1) {
      const angle = (segment / segments) * Math.PI * 2
      const index = ring * segments + segment
      position.setXYZ(index, Math.cos(angle) * radius, y, Math.sin(angle) * radius)
    }
  }
  position.needsUpdate = true
  geometry.computeBoundingSphere()
}

function setBeamTransform(
  beam: Mesh<BufferGeometry, MeshBasicMaterial>,
  origin: Vector3,
  target: StagePoint,
  beamAngleDegrees: number,
  aperture: number,
  visualKind: FixtureVisualKind,
) {
  const previewBeamAngleDegrees =
    beamAngleDegrees > 0 ? beamAngleDegrees : visualKind === FixtureVisualKind.EFFECT ? 5 : 25
  const targetVector = new Vector3(target.x, target.y, target.z)
  const length = Math.max(0.05, origin.distanceTo(targetVector))
  const towardSource = origin.clone().sub(targetVector).normalize()
  const halfAngle = (Math.max(1, Math.min(170, previewBeamAngleDegrees)) * Math.PI) / 360
  const sourceRadius = Math.max(0.003, aperture / 2)
  const targetRadius = sourceRadius + Math.tan(halfAngle) * length
  updateBeamGeometry(beam.geometry, length, sourceRadius, targetRadius)
  beam.position.lerpVectors(origin, targetVector, 0.5)
  beam.quaternion.setFromUnitVectors(UP, towardSource)
}

function disposeScene(runtime: StageRuntime) {
  runtime.controls.stopListenToKeyEvents()
  runtime.controls.dispose()
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

function applyMountRotation(root: Group, fixture: FixtureConfig) {
  const placement = fixture.stagePlacement
  if (!placement) return
  root.rotation.set(
    placement.rotationXDegrees * DEGREES_TO_RADIANS,
    placement.rotationYDegrees * DEGREES_TO_RADIANS,
    placement.rotationZDegrees * DEGREES_TO_RADIANS,
  )
}

function createHousingMaterial(color?: number) {
  return new MeshStandardMaterial({
    color: color && color > 0 ? color : 0x70797c,
    roughness: 0.48,
    metalness: 0.62,
  })
}

type ModelHierarchyVisual = {
  readonly materials: readonly MeshStandardMaterial[]
  readonly nodesById: ReadonlyMap<string, Group>
  readonly markersByEmitter: ReadonlyMap<
    string,
    ReadonlyMap<
      FixtureModelNodeKind,
      { readonly metadata: FixtureModelNode; readonly group: Group }
    >
  >
  readonly panPivot?: Group
  readonly tiltPivot?: Group
}

function hasModelNodeKind(nodes: readonly FixtureModelNode[], kind: FixtureModelNodeKind): boolean {
  return nodes.some((node) => node.kind === kind || hasModelNodeKind(node.children, kind))
}

function createModelHierarchy(
  nodes: readonly FixtureModelNode[],
  root: Group,
): ModelHierarchyVisual {
  const materials: MeshStandardMaterial[] = []
  const nodesById = new Map<string, Group>()
  const markersByEmitter = new Map<
    string,
    Map<FixtureModelNodeKind, { readonly metadata: FixtureModelNode; readonly group: Group }>
  >()
  let panPivot: Group | undefined
  let tiltPivot: Group | undefined

  const createNode = (metadata: FixtureModelNode, parent: Group) => {
    const group = new Group()
    group.name = metadata.name
    const transform = metadata.transform
    if (transform) {
      group.position.set(
        transform.xM * FIXTURE_SCALE,
        transform.yM * FIXTURE_SCALE,
        transform.zM * FIXTURE_SCALE,
      )
      group.quaternion.set(
        transform.quaternionX,
        transform.quaternionY,
        transform.quaternionZ,
        transform.quaternionW,
      )
      group.scale.set(transform.scaleX, transform.scaleY, transform.scaleZ)
    }
    parent.add(group)
    nodesById.set(metadata.id, group)
    if (metadata.emitterId) {
      const markers = markersByEmitter.get(metadata.emitterId) ?? new Map()
      markers.set(metadata.kind, { metadata, group })
      markersByEmitter.set(metadata.emitterId, markers)
    }
    if (!panPivot && metadata.kind === FixtureModelNodeKind.PAN_AXIS) panPivot = group
    if (!tiltPivot && metadata.kind === FixtureModelNodeKind.TILT_AXIS) tiltPivot = group

    const meshMetadata = metadata.mesh
    if (meshMetadata && meshMetadata.vertices.length >= 9 && meshMetadata.indices.length >= 3) {
      const geometry = new BufferGeometry()
      geometry.setAttribute("position", new Float32BufferAttribute(meshMetadata.vertices, 3))
      if (meshMetadata.normals.length === meshMetadata.vertices.length) {
        geometry.setAttribute("normal", new Float32BufferAttribute(meshMetadata.normals, 3))
      } else {
        geometry.computeVertexNormals()
      }
      geometry.setIndex(meshMetadata.indices)
      geometry.computeBoundingSphere()
      const material = createHousingMaterial(meshMetadata.colorRgb)
      const mesh = new Mesh(geometry, material)
      mesh.scale.setScalar(FIXTURE_SCALE)
      group.add(mesh)
      materials.push(material)
    }
    metadata.children.forEach((child) => createNode(child, group))
  }

  nodes.forEach((node) => createNode(node, root))
  return { materials, nodesById, markersByEmitter, panPivot, tiltPivot }
}

function createEmitterVisual(
  metadata: FixtureEmitter,
  fallbackParent: Group,
  hierarchy: ModelHierarchyVisual | undefined,
  scene: Scene,
  lensRadiusOverride?: number,
) {
  const markers = hierarchy?.markersByEmitter.get(metadata.id)
  const beamMarker = markers?.get(FixtureModelNodeKind.BEAM)
  const diameterMarker = markers?.get(FixtureModelNodeKind.BEAM_DIAMETER)
  const clipMarker = markers?.get(FixtureModelNodeKind.BEAM_CLIP)
  const modelParent = metadata.modelNodeId
    ? hierarchy?.nodesById.get(metadata.modelNodeId)
    : undefined
  const opticalMarker = beamMarker ?? diameterMarker
  const parent = opticalMarker?.group ?? modelParent ?? fallbackParent
  const apertureMeters =
    diameterMarker && diameterMarker.metadata.beamDiameterM > 0
      ? diameterMarker.metadata.beamDiameterM
      : metadata.apertureM
  const lensMaterial = new MeshStandardMaterial({
    color: OFF_COLOR,
    emissive: OFF_COLOR,
    emissiveIntensity: 0,
    roughness: 0.18,
  })
  const lensRadius =
    lensRadiusOverride ?? Math.max(0.018, Math.min(0.075, apertureMeters * FIXTURE_SCALE * 0.5))
  const lens = new Mesh(new SphereGeometry(lensRadius, 18, 12), lensMaterial)
  if (!opticalMarker && !modelParent) {
    lens.position.set(
      metadata.xM * FIXTURE_SCALE,
      metadata.yM * FIXTURE_SCALE,
      metadata.zM * FIXTURE_SCALE,
    )
  }
  parent.add(lens)

  let beam: Mesh<BufferGeometry, MeshBasicMaterial> | undefined
  if (metadata.castsBeam) {
    const beamMaterial = new MeshBasicMaterial({
      color: OFF_COLOR,
      transparent: true,
      opacity: 0,
      depthWrite: false,
      side: DoubleSide,
      blending: AdditiveBlending,
    })
    beam = new Mesh(createBeamGeometry(), beamMaterial)
    beam.visible = false
    scene.add(beam)
  }
  const directionFrame = opticalMarker?.group.parent ?? fallbackParent
  const localDirection = opticalMarker
    ? OPTICAL_FORWARD.clone().applyQuaternion(opticalMarker.group.quaternion)
    : new Vector3(metadata.directionX, metadata.directionY, metadata.directionZ)
  if (localDirection.lengthSq() <= Number.EPSILON) localDirection.copy(OPTICAL_FORWARD)
  return {
    metadata,
    lens,
    lenses: [lens],
    beam,
    beamOrigin: clipMarker?.group ?? lens,
    beamClippingDistanceMeters: clipMarker?.metadata.beamClippingDistanceM ?? 0,
    directionFrame,
    localDirection: localDirection.normalize(),
    apertureMeters,
  } satisfies EmitterVisual
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
  applyMountRotation(root, fixture)
  scene.add(root)

  const label = createFixtureLabel(fixture.name)
  if (label) root.add(label.sprite)

  const modelNodes = fixtureType.visual?.modelNodes ?? []
  const usesImportedHierarchy =
    modelNodes.length > 0 &&
    hasModelNodeKind(modelNodes, FixtureModelNodeKind.PAN_AXIS) &&
    hasModelNodeKind(modelNodes, FixtureModelNodeKind.TILT_AXIS) &&
    Boolean(fixtureType.visual?.emitters.some((emitter) => emitter.modelNodeId))
  if (usesImportedHierarchy) {
    const hierarchy = createModelHierarchy(modelNodes, root)
    const emitterGroup = hierarchy.tiltPivot ?? root
    const emitters = (fixtureType.visual?.emitters ?? []).map((metadata) =>
      createEmitterVisual(metadata, emitterGroup, hierarchy, scene),
    )
    return {
      fixture,
      fixtureType,
      root,
      mount,
      panPivot: hierarchy.panPivot,
      tiltPivot: hierarchy.tiltPivot,
      importedAxes: true,
      panBaseQuaternion: hierarchy.panPivot?.quaternion.clone(),
      tiltBaseQuaternion: hierarchy.tiltPivot?.quaternion.clone(),
      emitterGroup,
      housingMaterials: hierarchy.materials,
      emitters,
      label,
      rotationPhase: 0,
    } satisfies FixtureVisual
  }

  const housingMaterials: MeshStandardMaterial[] = []
  const bodyMaterial = createHousingMaterial()
  housingMaterials.push(bodyMaterial)

  const baseHeight = dimensions.height * 0.3
  const base = new Mesh(
    new CylinderGeometry(dimensions.width * 0.45, dimensions.width * 0.5, baseHeight, 24),
    bodyMaterial,
  )
  base.position.y = -baseHeight / 2
  root.add(base)

  const panPivot = new Group()
  panPivot.position.y = -baseHeight
  root.add(panPivot)

  const armHeight = dimensions.height * 0.5
  const armWidth = Math.max(0.045, dimensions.width * 0.1)
  for (const side of [-1, 1]) {
    const arm = new Mesh(
      new BoxGeometry(armWidth, armHeight, dimensions.depth * 0.24),
      bodyMaterial,
    )
    arm.position.set(side * dimensions.width * 0.37, -armHeight / 2, 0)
    panPivot.add(arm)
  }

  const tiltPivot = new Group()
  tiltPivot.position.y = -armHeight * 0.58
  panPivot.add(tiltPivot)
  const headDepth = dimensions.depth * 0.7
  const headRadius = Math.min(dimensions.width * 0.31, dimensions.height * 0.2)
  const head = new Mesh(
    new CylinderGeometry(headRadius, headRadius * 0.95, headDepth, 28),
    bodyMaterial,
  )
  head.rotation.x = Math.PI / 2
  tiltPivot.add(head)

  const emitterGroup = new Group()
  emitterGroup.position.z = headDepth / 2 - dimensions.depth / 2
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
    directionX: 0,
    directionY: 0,
    directionZ: 1,
    castsBeam: true,
    apertureM: 0.08,
    modelNodeId: "",
    $typeName: "music_auto_show.v1.FixtureEmitter" as const,
  }
  const sourceLensRadius = Math.max(0.018, headRadius * 0.13)
  const emitters: readonly EmitterVisual[] = [
    createEmitterVisual(metadata, emitterGroup, undefined, scene, sourceLensRadius),
  ]
  return {
    fixture,
    fixtureType,
    root,
    mount,
    panPivot,
    tiltPivot,
    importedAxes: false,
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
  applyMountRotation(root, fixture)
  scene.add(root)

  const label = createFixtureLabel(fixture.name)
  if (label) root.add(label.sprite)

  const aimGroup = new Group()
  root.add(aimGroup)
  const placement = fixture.stagePlacement
  if (placement?.focusTargetEnabled) {
    root.updateWorldMatrix(true, false)
    const worldMountRotation = new Quaternion()
    root.getWorldQuaternion(worldMountRotation)
    const localDirection = new Vector3(
      placement.focusTargetXM - mount.x,
      placement.focusTargetYM - mount.y,
      placement.focusTargetZM - mount.z,
    )
      .normalize()
      .applyQuaternion(worldMountRotation.invert())
    if (localDirection.lengthSq() > Number.EPSILON) {
      aimGroup.quaternion.setFromUnitVectors(OPTICAL_FORWARD, localDirection)
    }
  }

  const modelNodes = fixtureType.visual?.modelNodes ?? []
  let hierarchy: ModelHierarchyVisual | undefined
  let emitterGroup: Group
  let housingMaterials: readonly MeshStandardMaterial[]
  if (modelNodes.length > 0) {
    hierarchy = createModelHierarchy(modelNodes, aimGroup)
    emitterGroup = aimGroup
    housingMaterials = hierarchy.materials
  } else {
    const materials: MeshStandardMaterial[] = []
    const bodyMaterial = createHousingMaterial()
    const modelKind = fixtureType.visual?.modelKind ?? FixtureModelKind.GENERIC
    const housing = new Mesh(
      new BoxGeometry(dimensions.width, dimensions.height, dimensions.depth),
      bodyMaterial,
    )
    aimGroup.add(housing)
    const frontDepth = Math.max(0.018, dimensions.depth * 0.08)
    const front = new Mesh(
      new BoxGeometry(dimensions.width * 0.94, dimensions.height * 0.88, frontDepth),
      bodyMaterial,
    )
    front.position.z = dimensions.depth / 2 + frontDepth / 2
    aimGroup.add(front)

    const bracketThickness = Math.max(0.018, dimensions.width * 0.045)
    const bracketHeight =
      modelKind === FixtureModelKind.DERBY_EFFECT
        ? dimensions.height * 0.68
        : dimensions.height * 0.48
    for (const side of [-1, 1]) {
      const arm = new Mesh(
        new BoxGeometry(bracketThickness, bracketHeight, bracketThickness),
        bodyMaterial,
      )
      arm.position.set(
        side * dimensions.width * 0.55,
        dimensions.height * 0.28,
        -dimensions.depth * 0.18,
      )
      aimGroup.add(arm)
    }
    const bracketTop = new Mesh(
      new BoxGeometry(dimensions.width * 1.15, bracketThickness, bracketThickness),
      bodyMaterial,
    )
    bracketTop.position.set(
      0,
      dimensions.height * 0.28 + bracketHeight / 2,
      -dimensions.depth * 0.18,
    )
    aimGroup.add(bracketTop)
    materials.push(bodyMaterial)
    emitterGroup = new Group()
    aimGroup.add(emitterGroup)
    housingMaterials = materials
  }

  const emitters = (fixtureType.visual?.emitters ?? []).map((metadata) =>
    createEmitterVisual(metadata, emitterGroup, hierarchy, scene),
  )
  return {
    fixture,
    fixtureType,
    root,
    mount,
    importedAxes: false,
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
  const controls = new OrbitControls(camera, canvas)
  controls.target.set(0, 1.35, 0)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.minDistance = 2.5
  controls.maxDistance = 18
  controls.maxPolarAngle = Math.PI * 0.96
  controls.keyPanSpeed = 14
  controls.keyRotateSpeed = 1.5
  controls.update()
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
  fixtures.forEach((fixture) => {
    const fixtureType = typeById.get(fixture.fixtureTypeId)
    if (!fixtureType?.visual) return
    const placement = fixture.stagePlacement
    const mount = new Vector3(placement?.xM ?? 0, placement?.yM ?? 0, placement?.zM ?? 0)
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
    controls,
    fixtures: fixtureVisuals,
    floorMaterial,
    trussMaterial,
    grid,
    reducedMotion: false,
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
  const axisRotation = new Quaternion()
  const directionRotation = new Quaternion()
  runtime.fixtures.forEach((visual, fixtureId) => {
    const state = stateById.get(fixtureId)
    const color = state ? fixtureColor(state) : { red: 0, green: 0, blue: 0 }
    const brightness = state ? fixtureBrightness(state) : 0
    const threeColor = new Color(color.red / 255, color.green / 255, color.blue / 255)
    const strobeRate = state ? 2 + (state.strobe / 255) * 23 : 0
    const strobePulse =
      runtime.reducedMotion ||
      !state ||
      state.strobe === 0 ||
      (elapsedSeconds * strobeRate) % 1 < 0.48

    if (state && visual.panPivot && visual.tiltPivot && visual.fixtureType.visual) {
      const metadata = visual.fixtureType.visual
      const panRadians =
        (physicalAxisValue(
          state.pan,
          state.panFine,
          metadata.panMinDegrees,
          metadata.panMaxDegrees,
        ) *
          Math.PI) /
        180
      const tiltRadians =
        (physicalAxisValue(
          state.tilt,
          state.tiltFine,
          metadata.tiltMinDegrees,
          metadata.tiltMaxDegrees,
        ) *
          Math.PI) /
        180
      if (visual.importedAxes && visual.panBaseQuaternion && visual.tiltBaseQuaternion) {
        visual.panPivot.quaternion
          .copy(visual.panBaseQuaternion)
          .multiply(axisRotation.setFromAxisAngle(UP, panRadians))
        visual.tiltPivot.quaternion
          .copy(visual.tiltBaseQuaternion)
          .multiply(axisRotation.setFromAxisAngle(TILT_AXIS, tiltRadians))
      } else {
        visual.panPivot.rotation.y = panRadians
        visual.tiltPivot.rotation.x = Math.PI / 2 - tiltRadians
      }
    }
    if (state) {
      visual.rotationPhase = runtime.reducedMotion
        ? 0
        : wrappedPhaseStep(visual.rotationPhase, state.effectRotation)
    }
    visual.root.updateWorldMatrix(true, true)

    const strobeEmitterCount = visual.emitters.filter(
      (emitter) => emitter.metadata.kind === FixtureEmitterKind.STROBE,
    ).length
    let strobeEmitterIndex = 0
    visual.emitters.forEach((emitter) => {
      const kind = emitter.metadata.kind
      const strobeEmitter = kind === FixtureEmitterKind.STROBE
      const patternLevel =
        state && strobeEmitter && state.effectPattern > 0
          ? strobePatternLevel(
              state.effectPattern,
              strobeEmitterIndex,
              strobeEmitterCount,
              elapsedSeconds,
              state.effectSpeed / 255,
              runtime.reducedMotion,
            )
          : 0
      if (strobeEmitter) strobeEmitterIndex += 1
      const emitterColor =
        strobeEmitter || kind === FixtureEmitterKind.WHITE ? new Color(1, 1, 1) : threeColor
      const emitterBrightness = strobeEmitter
        ? state?.effectPattern
          ? patternLevel
          : state && state.strobe > 0 && strobePulse
            ? Math.max(brightness, state.strobe / 255)
            : 0
        : strobePulse
          ? brightness
          : 0
      const photometricGain =
        emitter.metadata.beamIntensity > 0
          ? Math.max(0.35, Math.min(1.5, Math.sqrt(emitter.metadata.beamIntensity / 1000)))
          : 1
      emitter.lenses.forEach((lens) => {
        lens.material.color.copy(emitterBrightness > 0 ? emitterColor : OFF_COLOR)
        lens.material.emissive.copy(emitterBrightness > 0 ? emitterColor : OFF_COLOR)
        lens.material.emissiveIntensity = emitterBrightness * 3.5 * photometricGain
      })
      if (!emitter.beam) return
      emitter.beam.visible = Boolean(state && emitterBrightness > 0.02)
      emitter.beam.material.color.copy(emitterColor)
      emitter.beam.material.opacity = Math.min(
        0.34,
        (0.035 + emitterBrightness * 0.22) * photometricGain,
      )
      if (!state || !emitter.beam.visible || !visual.fixtureType.visual) return

      const origin = new Vector3()
      emitter.beamOrigin.getWorldPosition(origin)
      const rotatedDirection =
        visual.fixtureType.visual.kind === FixtureVisualKind.EFFECT
          ? rotatedEffectDirection(emitter.localDirection, visual.rotationPhase)
          : emitter.localDirection
      emitter.directionFrame.getWorldQuaternion(directionRotation)
      const worldDirection = new Vector3(rotatedDirection.x, rotatedDirection.y, rotatedDirection.z)
        .applyQuaternion(directionRotation)
        .normalize()
      origin.addScaledVector(worldDirection, emitter.beamClippingDistanceMeters * FIXTURE_SCALE)
      const target: StagePoint = beamTargetFromDirection(origin, worldDirection)
      const beamAngleDegrees = beamAngleFromZoom(
        emitter.metadata.beamAngleDegrees,
        state.zoom,
        visual.fixtureType.visual.zoomPhysicalFromDegrees,
        visual.fixtureType.visual.zoomPhysicalToDegrees,
      )
      setBeamTransform(
        emitter.beam,
        origin,
        target,
        beamAngleDegrees,
        emitter.apertureMeters * FIXTURE_SCALE,
        visual.fixtureType.visual.kind,
      )
    })
  })
  runtime.controls.update()
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
  const controlsDescriptionId = useId()
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

    runtime.controls.listenToKeyEvents(canvas)
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.code === "Equal" || event.code === "NumpadAdd") {
        event.preventDefault()
        runtime.controls.dollyIn(1.12)
        runtime.controls.update()
      } else if (event.code === "Minus" || event.code === "NumpadSubtract") {
        event.preventDefault()
        runtime.controls.dollyOut(1.12)
        runtime.controls.update()
      } else if (event.code === "Home") {
        event.preventDefault()
        runtime.controls.reset()
      }
    }
    canvas.addEventListener("keydown", handleKeyDown)

    const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)")
    const applyMotionPreference = () => {
      runtime.reducedMotion = reducedMotionQuery.matches
      runtime.controls.enableDamping = !runtime.reducedMotion
      runtime.controls.update()
    }
    applyMotionPreference()
    reducedMotionQuery.addEventListener("change", applyMotionPreference)

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
      const renderedStates = interpolatedFixtureStates(
        sampleLiveFrame(time),
        latestStatesRef.current,
      )
      updateFixtureVisuals(runtime, renderedStates, time / 1000)
      animationFrame = requestAnimationFrame(render)
    }
    animationFrame = requestAnimationFrame(render)

    return () => {
      cancelAnimationFrame(animationFrame)
      observer.disconnect()
      themeObserver.disconnect()
      canvas.removeEventListener("webglcontextlost", handleContextLost)
      canvas.removeEventListener("webglcontextrestored", handleContextRestored)
      canvas.removeEventListener("keydown", handleKeyDown)
      reducedMotionQuery.removeEventListener("change", applyMotionPreference)
      disposeScene(runtime)
    }
  }, [orderedFixtures, fixtureTypes])

  return (
    <div className="relative h-80 overflow-hidden bg-background">
      <canvas
        ref={canvasRef}
        className="block size-full cursor-grab touch-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-inset active:cursor-grabbing"
        aria-label="Interactive live 3D stage preview driven by grandMA2 body, emitter, beam, pan, and tilt metadata"
        aria-describedby={controlsDescriptionId}
        tabIndex={0}
      >
        Live 3D stage preview
      </canvas>
      {orderedFixtures.length > 0 && !webglUnavailable ? (
        <p
          id={controlsDescriptionId}
          className="pointer-events-none absolute bottom-2 left-2 bg-background/80 px-2 py-1 text-[10px] text-muted-foreground"
        >
          Drag to orbit · Scroll or pinch to zoom · Arrow keys pan · Shift + arrow keys orbit · +/−
          zoom · Home resets
        </p>
      ) : null}
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
