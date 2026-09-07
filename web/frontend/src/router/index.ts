import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/agents'
    },
    {
      path: '/agents',
      name: 'agents',
      component: () => import('@/views/AgentManage.vue')
    },
    {
      path: '/skills',
      name: 'skills',
      component: () => import('@/views/SkillSquare.vue')
    },
    {
      path: '/workflow',
      name: 'workflow',
      component: () => import('@/views/WorkflowCenter.vue')
    },
    {
      path: '/edge-platform',
      name: 'edge-platform',
      component: () => import('@/views/EdgePlatform.vue')
    },
    {
      path: '/model-management',
      name: 'model-management',
      component: () => import('@/views/ModelManagement.vue')
    },
    {
      path: '/mcp-management',
      name: 'mcp-management',
      component: () => import('@/views/MCPManagement.vue')
    }
  ]
})

export default router
