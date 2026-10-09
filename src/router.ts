import { createRouter, createWebHashHistory } from 'vue-router'
import WorkspaceView from './views/WorkspaceView.vue'
import ReportView from './views/ReportView.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: WorkspaceView },
    { path: '/reports', component: WorkspaceView },
    { path: '/bookmarks', component: WorkspaceView },
    { path: '/report/:id', component: ReportView },
    { path: '/share/:token', component: ReportView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
