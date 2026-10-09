<script setup lang="ts">
import { ref, watch } from 'vue'
import { useResearchStore } from '../stores/research'
import { taskRuns } from '../services/research'
import type { TaskRun } from '../types'
const store=useResearchStore(),runs=ref<TaskRun[]>([]),error=ref(''),fromStage=ref('')
async function refresh(){if(!store.taskId)return;try{runs.value=(await taskRuns(store.taskId)).runs;error.value=''}catch(e){error.value=e instanceof Error?e.message:'运行记录加载失败'}}
watch(()=>[store.taskId,store.step,store.lastTask?.status],refresh,{immediate:true})
</script>
<template>
  <section v-if="store.taskId" class="task-history"><div class="workspace-actions"><button v-if="store.running&&!store.paused" class="secondary-button" :disabled="store.actionBusy" @click="store.controlTask('pause')">暂停任务</button><button v-if="store.paused" class="primary-button" :disabled="store.actionBusy" @click="store.controlTask('resume')">继续任务</button><template v-if="store.lastTask?.status==='failed'||store.lastTask?.status==='cancelled'"><select v-model="fromStage" aria-label="重试起点"><option value="">从失败阶段重试</option><option v-for="stage in ['A','B','C','D']" :key="stage" :value="stage">从 {{ stage }} 阶段</option></select><button class="primary-button" :disabled="store.actionBusy||store.running" @click="store.controlTask('retry',fromStage||undefined)">重试任务</button></template><button class="text-button" @click="refresh">刷新阶段记录</button><span class="muted">{{ store.lastTask?.status==='paused'?'已暂停，可在刷新后继续':store.lastTask?.status }}</span></div><p v-if="error" class="upload-error" role="alert">{{ error }}</p><details><summary>A–D 阶段记录 · {{ runs.length }} 条</summary><p class="muted">{{ store.taskId }}</p><div v-for="(run,index) in runs" :key="run.id??index" class="evidence-card"><strong>{{ run.stage||run.name||`记录 ${index+1}` }} · {{ run.status||run.execution_status }}</strong><p>{{ run.message }}</p><small>{{ run.startedAt||run.createdAt||run.updatedAt }}</small><details><summary>查看输入输出与状态</summary><pre>{{ JSON.stringify(run,null,2) }}</pre></details></div></details></section>
</template>
