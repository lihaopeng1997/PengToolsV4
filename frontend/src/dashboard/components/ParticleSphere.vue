<script setup lang="ts">
/**
 * 首页主视觉「粒子球」：WebGL 点云球体 + 环绕的小卫星与光环，跟随指针轻微转动。
 * 球体粒子按 5 个阶段的需求数占比着色，右侧图例显示各阶段数量，点击在看板中只看该阶段。
 * - 关闭动效 / prefers-reduced-motion：只画一帧，不再动
 * - 页面隐藏或滚出视口：暂停渲染
 * - 没有 WebGL：退回静态渐变球（图例照常可点）
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { STAGE_DEFS, type StageKey } from '../types'

const props = defineProps<{
  counts: Partial<Record<StageKey, number>>
  focus?: StageKey | null
}>()

const emit = defineEmits<{
  (e: 'focusStage', key: StageKey | null): void
}>()

// 与需求管理页步骤条一致的阶段色（WebGL 需要具体 RGB，不能用 CSS 变量）
const STAGE_RGB: Record<StageKey, [number, number, number]> = {
  analysis: [0.66, 0.6, 0.96],
  develop: [0.42, 0.35, 0.85],
  testing: [0.82, 0.54, 0.12],
  release: [0.05, 0.65, 0.91],
  online: [0.14, 0.5, 0.46],
}

const canvas = ref<HTMLCanvasElement | null>(null)
const host = ref<HTMLElement | null>(null)
const webglOk = ref(true)
const hovered = ref<StageKey | null>(null)

const legend = computed(() => STAGE_DEFS.map((stage) => ({ ...stage, count: props.counts[stage.key] ?? 0 })))
const total = computed(() => legend.value.reduce((sum, s) => sum + s.count, 0))

const VS = `attribute vec3 a_pos; attribute float a_kind; attribute float a_seed;
uniform float u_time; uniform float u_hover; uniform float u_pr; uniform vec2 u_mouse; uniform vec2 u_res;
uniform vec4 u_cut0; uniform float u_cut4; uniform float u_focus;
uniform vec3 u_c0; uniform vec3 u_c1; uniform vec3 u_c2; uniform vec3 u_c3; uniform vec3 u_c4;
varying float v_alpha; varying vec3 v_color;
mat3 ry(float a){float c=cos(a),s=sin(a);return mat3(c,0.,-s,0.,1.,0.,s,0.,c);}
mat3 rx(float a){float c=cos(a),s=sin(a);return mat3(1.,0.,0.,0.,c,s,0.,-s,c);}
void main(){
  float br=1.+.022*sin(u_time*1.047)+.035*u_hover; vec3 p; float size; vec3 col; float bucket=-1.;
  mat3 view=rx(.32+u_mouse.y*.3)*ry(u_mouse.x*.5);
  if(a_kind<.5){
    float j=.035*sin(u_time*.9+a_seed*60.);
    p=view*ry(u_time*.12)*(a_pos*(1.+j)*br); size=2.6;
    if(a_seed<u_cut0.x){col=u_c0;bucket=0.;} else if(a_seed<u_cut0.y){col=u_c1;bucket=1.;}
    else if(a_seed<u_cut0.z){col=u_c2;bucket=2.;} else if(a_seed<u_cut0.w){col=u_c3;bucket=3.;}
    else if(a_seed<u_cut4){col=u_c4;bucket=4.;} else {col=vec3(.62,.58,.94);}
  } else if(a_kind<1.5){
    float g=u_time*.33; vec3 cen=vec3(cos(g)*1.75,.12*sin(g),sin(g)*1.0);
    p=view*(cen+a_pos*.2*br); size=1.7; col=vec3(.2,.66,.93);
  } else {
    float g=a_seed*6.2832; p=view*vec3(cos(g)*1.75,.12*sin(g),sin(g)*1.0); size=1.1; col=vec3(.62,.58,.9);
  }
  float z=3.6-p.z; vec2 q=p.xy/z*2.45;
  gl_Position=vec4(q.x*u_res.y/u_res.x,q.y,0.,1.);
  float depth=clamp((p.z+1.)/2.,0.,1.);
  float dim=(u_focus>=0. && a_kind<.5 && abs(bucket-u_focus)>.5)?.18:1.;
  v_alpha=mix(.38,1.,depth)*dim*(a_kind>1.5?.5:1.);
  v_color=mix(col*.75+.25,col,depth);
  gl_PointSize=size*u_pr*(.7+1.*depth)*(3.6/z);
}`

const FS = `precision mediump float; varying float v_alpha; varying vec3 v_color;
void main(){ float d=length(gl_PointCoord-.5); if(d>.5) discard;
  gl_FragColor=vec4(v_color, v_alpha*smoothstep(.5,.1,d)); }`

type Uniforms = Record<string, WebGLUniformLocation | null>
let gl: WebGLRenderingContext | null = null
let uniforms: Uniforms = {}
let pointCount = 0
let raf = 0
let lastFrame = 0
let elapsed = 0
let visible = true
let onScreen = true
const mouse = [0, 0]
const target = [0, 0]
let hover = 0
let observer: IntersectionObserver | null = null

function motionEnabled(): boolean {
  if (document.documentElement.getAttribute('data-motion') === 'disabled') return false
  if (document.body.classList.contains('motion-disabled')) return false
  return !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function compile(type: number, source: string): WebGLShader | null {
  if (!gl) return null
  const shader = gl.createShader(type)
  if (!shader) return null
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null
}

function build(): boolean {
  const el = canvas.value
  if (!el) return false
  gl = (el.getContext('webgl', { antialias: false, alpha: true, premultipliedAlpha: false })
    || el.getContext('experimental-webgl')) as WebGLRenderingContext | null
  if (!gl) return false
  const vs = compile(gl.VERTEX_SHADER, VS)
  const fs = compile(gl.FRAGMENT_SHADER, FS)
  if (!vs || !fs) return false
  const prog = gl.createProgram()
  if (!prog) return false
  gl.attachShader(prog, vs)
  gl.attachShader(prog, fs)
  gl.linkProgram(prog)
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return false
  gl.useProgram(prog)

  const main = 6500
  const moon = 900
  const ring = 600
  pointCount = main + moon + ring
  const pos = new Float32Array(pointCount * 3)
  const kind = new Float32Array(pointCount)
  const seed = new Float32Array(pointCount)
  const fib = (i: number, n: number): [number, number, number] => {
    const y = 1 - ((i + 0.5) / n) * 2
    const r = Math.sqrt(1 - y * y)
    const th = i * 2.399963
    return [Math.cos(th) * r, y, Math.sin(th) * r]
  }
  let k = 0
  for (let i = 0; i < main; i++, k++) { pos.set(fib(i, main), k * 3); kind[k] = 0; seed[k] = Math.random() }
  for (let i = 0; i < moon; i++, k++) { pos.set(fib(i, moon), k * 3); kind[k] = 1; seed[k] = Math.random() }
  for (let i = 0; i < ring; i++, k++) { pos.set([0, 0, 0], k * 3); kind[k] = 2; seed[k] = i / ring }
  const attr = (name: string, data: Float32Array, size: number) => {
    if (!gl) return
    const buffer = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW)
    const loc = gl.getAttribLocation(prog, name)
    gl.enableVertexAttribArray(loc)
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0)
  }
  attr('a_pos', pos, 3)
  attr('a_kind', kind, 1)
  attr('a_seed', seed, 1)
  gl.enable(gl.BLEND)
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA)
  for (const name of ['u_time', 'u_hover', 'u_pr', 'u_mouse', 'u_res', 'u_cut0', 'u_cut4', 'u_focus',
    'u_c0', 'u_c1', 'u_c2', 'u_c3', 'u_c4']) {
    uniforms[name] = gl.getUniformLocation(prog, name)
  }
  STAGE_DEFS.forEach((stage, index) => gl?.uniform3fv(uniforms[`u_c${index}`], STAGE_RGB[stage.key]))
  syncStageUniforms()
  return true
}

// 粒子按阶段占比分桶：没有需求时全部用默认紫
function syncStageUniforms(): void {
  if (!gl) return
  const counts = STAGE_DEFS.map((s) => props.counts[s.key] ?? 0)
  const sum = counts.reduce((a, b) => a + b, 0)
  const cuts: number[] = []
  let acc = 0
  for (const c of counts) {
    acc += sum > 0 ? c / sum : 0
    cuts.push(sum > 0 ? acc : 0)
  }
  gl.uniform4f(uniforms.u_cut0, cuts[0], cuts[1], cuts[2], cuts[3])
  gl.uniform1f(uniforms.u_cut4, cuts[4])
  const focusKey = hovered.value || props.focus
  const focusIndex = focusKey ? STAGE_DEFS.findIndex((s) => s.key === focusKey) : -1
  gl.uniform1f(uniforms.u_focus, focusIndex)
}

function draw(): void {
  const el = canvas.value
  if (!gl || !el) return
  const pr = Math.min(window.devicePixelRatio || 1, 2)
  const w = Math.max(1, Math.round(el.clientWidth * pr))
  const h = Math.max(1, Math.round(el.clientHeight * pr))
  if (el.width !== w || el.height !== h) {
    el.width = w
    el.height = h
    gl.viewport(0, 0, w, h)
  }
  gl.clearColor(0, 0, 0, 0)
  gl.clear(gl.COLOR_BUFFER_BIT)
  gl.uniform1f(uniforms.u_time, elapsed)
  gl.uniform1f(uniforms.u_hover, hover)
  gl.uniform1f(uniforms.u_pr, pr)
  gl.uniform2f(uniforms.u_mouse, mouse[0], mouse[1])
  gl.uniform2f(uniforms.u_res, w, h)
  gl.drawArrays(gl.POINTS, 0, pointCount)
}

function frame(now: number): void {
  raf = 0
  const dt = lastFrame ? Math.min(0.05, (now - lastFrame) / 1000) : 0
  lastFrame = now
  elapsed += dt
  mouse[0] += (target[0] - mouse[0]) * Math.min(1, dt * 3)
  mouse[1] += (target[1] - mouse[1]) * Math.min(1, dt * 3)
  draw()
  schedule()
}

function schedule(): void {
  if (raf || !gl || !visible || !onScreen || !motionEnabled()) return
  raf = requestAnimationFrame(frame)
}

function stop(): void {
  if (raf) cancelAnimationFrame(raf)
  raf = 0
  lastFrame = 0
}

function onVisibility(): void {
  visible = document.visibilityState !== 'hidden'
  if (visible) schedule()
  else stop()
}

function onPointer(event: PointerEvent): void {
  const el = canvas.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const x = ((event.clientX - rect.left) / rect.width) * 2 - 1
  const y = ((event.clientY - rect.top) / rect.height) * 2 - 1
  target[0] = Math.max(-1, Math.min(1, x))
  target[1] = Math.max(-1, Math.min(1, y))
  hover = Math.hypot(x, y) < 0.45 ? 1 : 0
  if (!motionEnabled()) draw()
}

function onLeave(): void {
  target[0] = 0
  target[1] = 0
  hover = 0
}

function pick(key: StageKey): void {
  emit('focusStage', props.focus === key ? null : key)
}

watch(() => [props.counts, props.focus, hovered.value], () => {
  syncStageUniforms()
  if (!raf) draw()
}, { deep: true })

onMounted(() => {
  if (!build()) {
    webglOk.value = false
    gl = null
    return
  }
  draw()
  document.addEventListener('visibilitychange', onVisibility)
  if (host.value && 'IntersectionObserver' in window) {
    observer = new IntersectionObserver((entries) => {
      onScreen = entries.some((entry) => entry.isIntersecting)
      if (onScreen) schedule()
      else stop()
    })
    observer.observe(host.value)
  }
  schedule()
})

onBeforeUnmount(() => {
  stop()
  document.removeEventListener('visibilitychange', onVisibility)
  observer?.disconnect()
  const lose = gl?.getExtension('WEBGL_lose_context')
  lose?.loseContext()
  gl = null
})
</script>

<template>
  <div ref="host" class="sphere-wrap" :class="{ focused: Boolean(props.focus) }" data-testid="particle-sphere">
    <div class="orb-area" @pointermove="onPointer" @pointerleave="onLeave">
      <canvas v-show="webglOk" ref="canvas" class="orb-canvas" aria-hidden="true" />
      <div v-if="!webglOk" class="orb-fallback" aria-hidden="true" />
      <div class="orb-glow" aria-hidden="true" />
      <div class="orb-center">
        <b>{{ total }}</b>
        <span>个需求</span>
      </div>
    </div>

    <ul class="legend" aria-label="需求阶段">
      <li v-for="s in legend" :key="s.key">
        <button
          type="button"
          class="legend-item"
          :class="{ on: props.focus === s.key, dim: (props.focus || hovered) && (props.focus || hovered) !== s.key, empty: !s.count }"
          :style="{ '--stage': s.color }"
          :data-stage="s.key"
          @click="pick(s.key)"
          @mouseenter="hovered = s.key"
          @mouseleave="hovered = null"
          @focus="hovered = s.key"
          @blur="hovered = null"
        >
          <span class="dot" />
          <span class="name">{{ s.label }}</span>
          <span class="count">{{ s.count }}</span>
        </button>
      </li>
      <li class="hint">{{ props.focus ? '再次点击取消筛选' : '点击阶段，在看板中只看它' }}</li>
    </ul>
  </div>
</template>

<style scoped>
.sphere-wrap {
  position: relative;
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 280px;
}

.orb-area {
  position: relative;
  height: 320px;
  min-width: 0;
}

.orb-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  cursor: grab;
}

.orb-glow {
  position: absolute;
  inset: 12% 18%;
  border-radius: 50%;
  background: radial-gradient(closest-side, var(--primary-soft), transparent 70%);
  filter: blur(8px);
  pointer-events: none;
  z-index: -1;
}

.orb-fallback {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 190px;
  height: 190px;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background:
    radial-gradient(circle at 35% 30%, rgba(255, 255, 255, 0.85), transparent 38%),
    radial-gradient(circle at 60% 65%, var(--accent-cyan), transparent 55%),
    radial-gradient(circle at 40% 45%, var(--primary), var(--aurora-end));
  opacity: 0.85;
}

.orb-center {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  pointer-events: none;
  color: var(--text-strong);
  text-shadow: 0 1px 10px var(--surface);
}

.orb-center b {
  font-size: 30px;
  line-height: 1;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}

.orb-center span {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 1px;
}

.legend {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 150px;
}

.legend-item {
  width: 100%;
  display: grid;
  grid-template-columns: 10px 1fr auto;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
  border-radius: 12px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  transition: background 160ms ease, border-color 160ms ease, opacity 160ms ease;
}

.legend-item:hover,
.legend-item:focus-visible,
.legend-item.on {
  background: var(--surface);
  border-color: var(--stage);
  outline: none;
}

.legend-item.dim {
  opacity: 0.45;
}

.legend-item.empty .count {
  color: var(--text-muted);
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--stage);
  box-shadow: 0 0 0 3px var(--surface-soft);
}

.name {
  text-align: left;
  font-weight: 600;
}

.count {
  font-size: 18px;
  font-weight: 800;
  color: var(--stage);
  font-variant-numeric: tabular-nums;
}

.hint {
  margin-top: 4px;
  padding-left: 12px;
  font-size: 11px;
  color: var(--text-muted);
}

@media (max-width: 760px) {
  .sphere-wrap {
    grid-template-columns: 1fr;
  }

  .orb-area {
    height: 240px;
  }
}
</style>
