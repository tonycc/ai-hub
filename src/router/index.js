import { createRouter, createWebHashHistory } from 'vue-router'
import PlatformLayout from '../layouts/PlatformLayout.vue'
import { platformServices } from '../data/platformCapabilities'

const routes = [
  {
    path: '/',
    component: PlatformLayout,
    children: [
      {
        path: '',
        name: 'portal',
        component: () => import('../views/PortalView.vue'),
        meta: { title: '平台首页', breadcrumb: ['平台门户', '平台首页'] },
      },
      {
        path: 'platform/notifications',
        name: 'notifications',
        component: () => import('../views/MessageCenterView.vue'),
        meta: { title: '通知中心', breadcrumb: ['平台治理', '通知中心'] },
      },
      {
        path: 'applications',
        name: 'applications',
        component: () => import('../views/ApplicationsView.vue'),
        meta: { title: '应用中心', breadcrumb: ['平台能力', '应用中心'] },
      },
      {
        path: 'platform',
        name: 'platform-capabilities',
        component: () => import('../views/PlatformCapabilitiesView.vue'),
        meta: { title: '平台统一能力', breadcrumb: ['平台能力', '能力总览'] },
      },
      {
        path: 'platform/:service(identity|permissions|settings|audit|operations|developer)',
        name: 'platform-service',
        component: () => import('../views/PlatformServiceView.vue'),
        meta: { title: '公共服务', breadcrumb: ['平台能力', '公共服务'] },
      },
      {
        path: 'platform/integrations',
        name: 'integrations',
        component: () => import('../views/CollaborationView.vue'),
        meta: { title: '接入治理', breadcrumb: ['平台治理', '接入治理'] },
      },
      {
        path: 'semantics',
        name: 'semantics',
        component: () => import('../views/SemanticCenterView.vue'),
        meta: { title: '企业语义中心', breadcrumb: ['平台能力', '企业语义中心'] },
      },
      {
        path: 'ai-center',
        name: 'ai-center',
        component: () => import('../views/AiCenterView.vue'),
        meta: { title: 'AI 员工中心', breadcrumb: ['平台能力', 'AI 员工中心'] },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const serviceTitle = to.name === 'platform-service' ? platformServices[to.params.service]?.title : null
  document.title = `${serviceTitle || to.meta.title || 'AI Hub'} · AI Hub`
})

export default router
