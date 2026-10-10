<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ScenePart, SceneSpec } from '../types'
const props = defineProps<{ scene: SceneSpec; editable?: boolean }>()
const emit = defineEmits<{ change: [scene: SceneSpec] }>()
const canvas = ref<HTMLCanvasElement>()
const yaw = ref(.55), pitch = ref(-.3), zoom = ref(1), explode = ref(0)
const selectedId = ref(''), hidden = ref<string[]>([])
const importError = ref('')
const selected = computed(() => props.scene.parts.find(p => p.id === selectedId.value))
type Vec = [number, number, number]
type Mesh = { vertices: Vec[]; faces: number[][] }
let resize: ResizeObserver | undefined
let dragging: { x: number; y: number } | undefined
const finite = (value: number, fallback: number) => Number.isFinite(value) ? value : fallback
function mesh(part: ScenePart): Mesh {
  if (part.geometry === 'box') return { vertices: [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]], faces: [[0,1,2,3],[4,7,6,5],[0,4,5,1],[3,2,6,7],[1,5,6,2],[0,3,7,4]] }
  const vertices: Vec[] = [], faces: number[][] = [], segments = 16
  if (part.geometry === 'sphere') {
    for (let y=0;y<=10;y++) for(let x=0;x<segments;x++) { const a=y/10*Math.PI,b=x/segments*Math.PI*2; vertices.push([Math.sin(a)*Math.cos(b),Math.cos(a),Math.sin(a)*Math.sin(b)]) }
    for(let y=0;y<10;y++) for(let x=0;x<segments;x++) faces.push([y*segments+x,y*segments+(x+1)%segments,(y+1)*segments+(x+1)%segments,(y+1)*segments+x])
  } else {
    for(const y of [-1,1]) for(let x=0;x<segments;x++) vertices.push([Math.cos(x/segments*Math.PI*2),y,Math.sin(x/segments*Math.PI*2)])
    faces.push(Array.from({length:segments},(_,i)=>i),Array.from({length:segments},(_,i)=>segments+i))
    for(let x=0;x<segments;x++) faces.push([x,(x+1)%segments,segments+(x+1)%segments,segments+x])
  }
  return {vertices,faces}
}
function rotate([x,y,z]: Vec): Vec { const a=x*Math.cos(yaw.value)+z*Math.sin(yaw.value),b=-x*Math.sin(yaw.value)+z*Math.cos(yaw.value); return [a,y*Math.cos(pitch.value)-b*Math.sin(pitch.value),y*Math.sin(pitch.value)+b*Math.cos(pitch.value)] }
function draw() {
  const element=canvas.value,ctx=element?.getContext('2d'); if(!element||!ctx) return
  const width=Math.max(280,element.clientWidth),height=360,dpr=Math.min(devicePixelRatio||1,2)
  element.width=width*dpr;element.height=height*dpr;ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height)
  const all=props.scene.parts,extent=Math.max(1,...all.flatMap(p=>p.position.map((v,i)=>Math.abs(finite(v,0))+Math.abs(finite(p.size[i]??1,1)))))
  const scale=Math.min(width,height)*.29/extent*zoom.value
  const project=(v:Vec)=>{ const [x,y,z]=rotate(v),perspective=1/(1+z/(extent*8));return {x:width/2+x*scale*perspective,y:height/2-y*scale*perspective,z} }
  const faces:{points:ReturnType<typeof project>[];color:string;part:string;depth:number}[]=[],centers=new Map<string,ReturnType<typeof project>>()
  all.forEach((part,index)=>{
    if(hidden.value.includes(part.id))return
    const offset:Vec=part.position.map((v,i)=>finite(v,0)*(1+explode.value)+(i===0&&part.position.every(v=>v===0)?(index-(all.length-1)/2)*explode.value:0)) as Vec
    const center=project(offset);centers.set(part.id,center)
    const geometry=mesh(part),points=geometry.vertices.map(v=>project(v.map((n,i)=>n*Math.abs(finite(part.size[i]??1,1))/2+offset[i]!) as Vec))
    geometry.faces.forEach(face=>{const ps=face.map(i=>points[i]!);faces.push({points:ps,color:/^#[0-9a-f]{6}$/i.test(part.color)?part.color:'#69b1ff',part:part.id,depth:ps.reduce((n,p)=>n+p.z,0)/ps.length})})
  })
  ctx.strokeStyle='#93a3b8';ctx.setLineDash([4,4]);for(const relation of props.scene.relations??[]){const a=centers.get(relation.source),b=centers.get(relation.target);if(a&&b){ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke()}}ctx.setLineDash([])
  faces.sort((a,b)=>b.depth-a.depth).forEach(face=>{ctx.beginPath();face.points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.closePath();ctx.fillStyle=face.color;ctx.globalAlpha=face.part===selectedId.value?1:.82;ctx.fill();ctx.globalAlpha=1;ctx.strokeStyle=face.part===selectedId.value?'#003eb3':'#ffffff80';ctx.lineWidth=face.part===selectedId.value?2:1;ctx.stroke()})
  ctx.font='12px system-ui';ctx.textAlign='center';all.forEach(part=>{const p=centers.get(part.id);if(!p)return;const text=part.label.slice(0,28),w=ctx.measureText(text).width+12;ctx.fillStyle='#ffffffed';ctx.fillRect(p.x-w/2,p.y-9,w,19);ctx.fillStyle='#172554';ctx.fillText(text,p.x,p.y+5)})
}
function start(e:PointerEvent){dragging={x:e.clientX,y:e.clientY};canvas.value?.setPointerCapture(e.pointerId)}
function move(e:PointerEvent){if(!dragging)return;yaw.value+=(e.clientX-dragging.x)*.009;pitch.value=Math.max(-1.5,Math.min(1.5,pitch.value+(e.clientY-dragging.y)*.009));dragging={x:e.clientX,y:e.clientY}}
function keyboard(e:KeyboardEvent){if(e.key.startsWith('Arrow')){e.preventDefault();if(e.key==='ArrowLeft')yaw.value-=.15;if(e.key==='ArrowRight')yaw.value+=.15;if(e.key==='ArrowUp')pitch.value-=.15;if(e.key==='ArrowDown')pitch.value+=.15}}
function update(field:keyof ScenePart,value:unknown){if(!selected.value)return;emit('change',{...props.scene,parts:props.scene.parts.map(p=>p.id===selectedId.value?{...p,[field]:value}:p)})}
function vector(field:'position'|'size',index:number,event:Event){if(!selected.value)return;const next:[number,number,number]=[...selected.value[field]],number=Number((event.target as HTMLInputElement).value);if(!Number.isFinite(number))return;next[index]=field==='size'?Math.max(.05,Math.min(100,number)):Math.max(-100,Math.min(100,number));update(field,next)}
function addPart(){const id=`part-${crypto.randomUUID().slice(0,8)}`;emit('change',{...props.scene,parts:[...props.scene.parts,{id,label:'新部件',geometry:'box',position:[0,0,0],size:[1,1,1],color:'#69b1ff',dimensions_known:false}]});selectedId.value=id}
function removePart(){emit('change',{...props.scene,parts:props.scene.parts.filter(p=>p.id!==selectedId.value),relations:props.scene.relations?.filter(r=>r.source!==selectedId.value&&r.target!==selectedId.value)});selectedId.value=''}
async function importScene(event:Event){
  const input=event.target as HTMLInputElement,file=input.files?.[0];if(!file)return
  importError.value=''
  try{
    if(file.size>1024*1024)throw new Error('三维 JSON 包不能超过 1 MB。')
    const parsed=JSON.parse(await file.text()),value=parsed.scene??parsed
    if(!value||!Array.isArray(value.parts)||!value.parts.length||value.parts.length>100)throw new Error('需要包含 1–100 个部件的 SceneSpec JSON。')
    const ids=new Set<string>()
    for(const part of value.parts){
      if(typeof part.id!=='string'||!part.id||ids.has(part.id)||typeof part.label!=='string'||!['box','sphere','cylinder'].includes(part.geometry))throw new Error('部件需要唯一 ID、名称及 box/sphere/cylinder 几何体。')
      ids.add(part.id)
      for(const key of ['position','size'])if(!Array.isArray(part[key])||part[key].length!==3||part[key].some((v:unknown)=>typeof v!=='number'||!Number.isFinite(v)||Math.abs(v)>100||(key==='size'&&v<=0)))throw new Error('位置与尺寸须为三个有限数值，绝对值不超过 100，尺寸须为正。')
    }
    const scene:SceneSpec={...value,id:typeof value.id==='string'?value.id:crypto.randomUUID(),title:typeof value.title==='string'?value.title:'导入概念结构',kind:'schematic',parts:value.parts.map((p:ScenePart)=>({...p,color:/^#[0-9a-f]{6}$/i.test(p.color)?p.color:'#69b1ff',dimensions_known:false})),relations:Array.isArray(value.relations)?value.relations.filter((r:{source?:string;target?:string})=>ids.has(r.source??'')&&ids.has(r.target??'')):[]}
    emit('change',scene);selectedId.value='';hidden.value=[]
  }catch(e){importError.value=e instanceof Error?e.message:'三维包导入失败'}finally{input.value=''}
}
function exportScene(){const url=URL.createObjectURL(new Blob([JSON.stringify(props.scene,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=`${props.scene.id.replace(/[^a-zA-Z0-9_-]/g,'_')}.scene.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000)}
watch([()=>props.scene,yaw,pitch,zoom,explode,hidden,selectedId],draw,{deep:true,flush:'post'})
onMounted(()=>{resize=new ResizeObserver(draw);if(canvas.value)resize.observe(canvas.value);draw()})
onBeforeUnmount(()=>resize?.disconnect())
</script>
<template>
  <section class="scene-viewer"><h3>{{ scene.title }}</h3><p class="muted">归一化概念结构示意 · 非真实尺寸或实体模型验证。拖动旋转、滚轮缩放；也可聚焦画布后使用方向键。</p>
    <canvas ref="canvas" class="scene-canvas" tabindex="0" aria-label="可旋转三维结构" :data-yaw="yaw" :data-pitch="pitch" :data-parts="scene.parts.length" @pointerdown="start" @pointermove="move" @pointerup="dragging=undefined" @pointercancel="dragging=undefined" @keydown="keyboard" @wheel.prevent="zoom=Math.max(.3,Math.min(3,zoom-$event.deltaY*.002))">三维概念模型；部件及说明列于下方。</canvas>
    <div class="scene-controls"><label>缩放<input v-model.number="zoom" type="range" min="0.3" max="3" step="0.05" aria-label="三维缩放" /></label><label>爆炸视图<input v-model.number="explode" type="range" min="0" max="2" step="0.05" aria-label="爆炸视图" /></label><button class="secondary-button" @click="yaw=.55;pitch=-.3;zoom=1;explode=0">重置视角</button></div>
    <div class="part-list"><button v-for="part in scene.parts" :key="part.id" :class="{active:selectedId===part.id}" :aria-pressed="selectedId===part.id" @click="selectedId=part.id"><span :style="{background:part.color}"></span>{{ part.label }}</button></div>
    <div v-if="selected" class="part-detail"><strong>{{ selected.label }}</strong><p>{{ selected.description || '尚无部件说明。' }}</p><p class="muted">证据：{{ selected.evidence_refs?.join('、') || '未关联，示意部件' }}</p><label><input v-model="hidden" type="checkbox" :value="selected.id" /> 隐藏此部件</label>
      <div v-if="editable" class="field-grid"><label>部件名称<input :value="selected.label" aria-label="部件名称" @input="update('label',($event.target as HTMLInputElement).value)" /></label><label>几何体<select :value="selected.geometry" aria-label="部件几何体" @change="update('geometry',($event.target as HTMLSelectElement).value)"><option value="box">长方体</option><option value="sphere">球体</option><option value="cylinder">圆柱体</option></select></label><label>颜色<input type="color" :value="selected.color" aria-label="部件颜色" @input="update('color',($event.target as HTMLInputElement).value)" /></label><div v-for="field in (['position','size'] as const)" :key="field" class="vector-field"><span>{{ field==='position'?'位置':'示意尺寸' }}</span><label v-for="(axis,index) in ['X','Y','Z']" :key="axis">{{ axis }}<input type="number" step="0.1" :value="selected[field][index]" :aria-label="`${field==='position'?'位置':'尺寸'} ${axis}`" @change="vector(field,index,$event)" /></label></div><button class="text-button" @click="removePart">移除此部件</button></div>
    </div><div class="workspace-actions"><button v-if="editable" class="secondary-button" @click="addPart">添加示意部件</button><button class="secondary-button" @click="exportScene">导出三维 JSON 包</button></div><label v-if="editable" class="source-upload">导入三维 JSON 包<input type="file" accept=".json" aria-label="导入三维 JSON 包" @change="importScene"/></label><p v-if="importError" class="upload-error" role="alert">{{ importError }}</p><details v-if="scene.unknowns?.length"><summary>未知参数与限制</summary><p v-for="(item,i) in scene.unknowns" :key="i">{{ typeof item==='string'?item:JSON.stringify(item) }}</p></details>
  </section>
</template>
