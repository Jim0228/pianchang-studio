import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    { path: '/', name: 'dashboard', component: () => import('./views/DashboardView.vue') },
    { path: '/projects/new', name: 'wizard', component: () => import('./views/WizardView.vue') },
    { path: '/projects/:id', name: 'project', component: () => import('./views/ProjectView.vue'), props: true },
    { path: '/skills', name: 'skills', component: () => import('./views/SkillsView.vue') },
    { path: '/tasks', name: 'tasks', component: () => import('./views/TasksView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

export default router
