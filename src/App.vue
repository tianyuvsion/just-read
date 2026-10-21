<script setup lang="ts">
import { useRoute } from 'vue-router'
import { auth } from './services/auth'
import AuthenticatedShell from './components/AuthenticatedShell.vue'
const route = useRoute()
</script>

<template>
  <RouterView v-if="route.meta.public" />
  <AuthenticatedShell v-else-if="auth.user" />
  <div v-else class="session-check" role="status">正在确认登录状态…</div>
</template>

<style scoped>
.session-check{min-height:100dvh;display:grid;place-items:center;background:#f5f7fa;color:#718399;font-size:14px}
</style>
