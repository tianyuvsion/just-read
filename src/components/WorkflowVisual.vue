<script setup lang="ts">
import { computed, ref } from 'vue'
import type { VisualSpec } from '../types'
import ReportChart from './ReportChart.vue'
const props=defineProps<{ visual:VisualSpec }>()
const selected=ref('')
const palette=['#1677ff','#13a8a8','#722ed1','#d48806','#eb2f96']
const nodes=computed(()=>props.visual.nodes??[])
const width=760
const height=computed(()=>props.visual.kind==='flow'?Math.max(220,Math.ceil(nodes.value.length/3)*115):460)
const positioned=computed(()=>nodes.value.map((n,i)=>({...n,x:props.visual.kind==='flow'?130+(i%3)*250:width/2+Math.cos(i/Math.max(1,nodes.value.length)*2*Math.PI)*260,y:props.visual.kind==='flow'?70+Math.floor(i/3)*115:height.value/2+Math.sin(i/Math.max(1,nodes.value.length)*2*Math.PI)*165})))
const edges=computed(()=>(props.visual.edges??[]).flatMap(e=>{const a=positioned.value.find(n=>n.id===e.source),b=positioned.value.find(n=>n.id===e.target);return a&&b?[{...e,a,b}]:[]}))
const series=computed(()=>props.visual.series??[])
const labels=computed(()=>props.visual.labels??[])
const values=computed(()=>series.value.flatMap(s=>s.values).filter(Number.isFinite))
const scale=computed(()=>Math.max(1,...values.value.map(Math.abs)))
const minimum=computed(()=>Math.min(0,...values.value.map(v=>v/scale.value)))
const maximum=computed(()=>Math.max(0,...values.value.map(v=>v/scale.value)))
function point(value:number,index:number){return {x:65+index/Math.max(1,labels.value.length-1)*630,y:320-(value/scale.value-minimum.value)/(maximum.value-minimum.value||1)*255}}
function line(values:number[]){return values.map((v,i)=>Number.isFinite(v)?`${point(v,i).x},${point(v,i).y}`:'').filter(Boolean).join(' ')}
const bars=computed(()=>series.value.flatMap(s=>s.values.flatMap((value,i)=>Number.isFinite(value)?[{label:`${labels.value[i]??i+1}${series.value.length>1?` · ${s.name}`:''}`,value}]:[])))
const marker=computed(()=>`arrow-${props.visual.id.replace(/[^a-zA-Z0-9_-]/g,'_')}`)
</script>
<template>
  <figure class="workflow-visual" :data-visual-kind="visual.kind"><figcaption><strong>{{ visual.title }}</strong><span class="muted">{{ visual.period }} {{ visual.unit ? `· ${visual.unit}` : '' }}</span></figcaption>
    <template v-if="visual.kind==='flow'||visual.kind==='relation'"><svg class="diagram-svg" :viewBox="`0 0 ${width} ${height}`" role="img" :aria-label="visual.title"><defs><marker :id="marker" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#7d91ad" /></marker></defs><g v-for="(edge,i) in edges" :key="i"><line :x1="edge.a.x" :y1="edge.a.y" :x2="edge.b.x" :y2="edge.b.y" stroke="#7d91ad" stroke-width="2" :marker-end="`url(#${marker})`"/><text :x="(edge.a.x+edge.b.x)/2" :y="(edge.a.y+edge.b.y)/2-10" text-anchor="middle" class="edge-label">{{ edge.label||edge.type }}</text></g><g v-for="node in positioned" :key="node.id" class="diagram-node" tabindex="0" role="button" :aria-label="`图解节点：${node.label}`" @click="selected=node.id" @keydown.enter="selected=node.id"><rect :x="node.x-99" :y="node.y-25" width="198" height="50" rx="10" :fill="selected===node.id?'#bae0ff':'#f0f7ff'" stroke="#91caff"/><text :x="node.x" :y="node.y+5" text-anchor="middle">{{ node.label.length>15?node.label.slice(0,14)+'…':node.label }}</text><title>{{ node.label }}</title></g></svg><div v-if="selected" class="diagram-selection"><strong>{{ nodes.find(n=>n.id===selected)?.label }}</strong><p v-for="(edge,i) in edges.filter(e=>e.source===selected||e.target===selected)" :key="i">{{ edge.a.label }} → {{ edge.b.label }} · {{ edge.label||edge.type }} <small>证据 {{ edge.evidence_refs?.join('、')||'待关联' }}</small></p></div></template>
    <ReportChart v-else-if="visual.kind==='bar'" :data="bars" :meta="{title:visual.title,unit:visual.unit||'未提供单位',note:'候选数据，待核实'}" />
    <template v-else-if="visual.kind==='line'"><svg class="line-svg" viewBox="0 0 760 390" role="img" :aria-label="visual.title"><line x1="65" y1="320" x2="710" y2="320" stroke="#aab7c7"/><line x1="65" y1="45" x2="65" y2="320" stroke="#aab7c7"/><text x="58" y="65" text-anchor="end">{{ maximum*scale }}</text><text x="58" y="320" text-anchor="end">{{ minimum*scale }}</text><g v-for="(item,si) in series" :key="item.name"><polyline :points="line(item.values)" fill="none" :stroke="palette[si%palette.length]" stroke-width="3"/><template v-for="(value,index) in item.values" :key="index"><circle v-if="Number.isFinite(value)" :cx="point(value,index).x" :cy="point(value,index).y" r="5" :fill="palette[si%palette.length]"><title>{{ item.name }} · {{ labels[index] }}：{{ value }} {{ visual.unit }}</title></circle></template></g><text v-for="(label,i) in labels" :key="i" :x="point(0,i).x" y="345" text-anchor="middle">{{ label.slice(0,12) }}</text></svg><p v-for="(item,i) in series" :key="i" :style="{color:palette[i%palette.length]}">● {{ item.name }}</p><p class="muted">候选数据，待核实；完整数值见下方表格。</p></template>
    <div v-if="visual.kind==='line'||visual.kind==='table'" class="table-scroll"><table><caption class="sr-only">{{ visual.title }} 数据</caption><thead><tr><th>项目</th><th v-for="item in series" :key="item.name">{{ item.name }} {{ visual.unit }}</th></tr></thead><tbody><tr v-for="(label,i) in labels" :key="i"><th>{{ label }}</th><td v-for="item in series" :key="item.name">{{ item.values[i] ?? '—' }}</td></tr></tbody></table></div>
    <details v-if="visual.provenance"><summary>图解来源</summary><pre>{{ JSON.stringify(visual.provenance,null,2) }}</pre></details>
  </figure>
</template>
