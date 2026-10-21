import { createRouter, createWebHashHistory } from 'vue-router'
import WorkspaceView from './views/WorkspaceView.vue'
import ReportView from './views/ReportView.vue'
import ChatView from './views/ChatView.vue'
import LoginView from './views/LoginView.vue'
import { auth, checkSession, safeReturnPath } from './services/auth'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: LoginView, meta: { public: true } },
    { path: '/', component: WorkspaceView },
    { path: '/reports', component: WorkspaceView },
    { path: '/bookmarks', component: WorkspaceView },
    { path: '/chat', component: ChatView },
    { path: '/report/:id', component: ReportView },
    { path: '/share/:token', component: ReportView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async to => {
  await checkSession()
  if (to.meta.public) return auth.user ? safeReturnPath(to.query.redirect) : true
  if (auth.user) return true
  auth.redirect = safeReturnPath(to.fullPath)
  auth.guideOpen = true
  return { path: '/login', query: { redirect: auth.redirect } }
})

window.addEventListener('justread:login-required', () => {
  void router.replace({ path: '/login', query: { redirect: auth.redirect } })
})
