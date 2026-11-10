import { createRouter, createWebHashHistory } from 'vue-router'
import PlatformLayout from '../layouts/PlatformLayout.vue'

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
        path: 'applications',
        name: 'applications',
        component: () => import('../views/ApplicationsView.vue'),
        meta: { title: '应用中心', breadcrumb: ['平台能力', '应用中心'] },
      },
      {
        path: 'platform/identity',
        name: 'identity',
        component: () => import('../views/IdentityManagementView.vue'),
        meta: { title: '用户与组织', breadcrumb: ['平台治理', '用户与组织'] },
      },
      {
        path: 'platform/audit',
        name: 'audit',
        component: () => import('../views/AuditCenterView.vue'),
        meta: { title: '审计中心', breadcrumb: ['平台治理', '审计中心'] },
      },
      {
        path: 'platform/operations',
        name: 'operations',
        component: () => import('../views/OperationsCenterView.vue'),
        meta: { title: '运维中心', breadcrumb: ['运行与研发', '运维中心'] },
      },
      {
        path: 'platform/ingest',
        name: 'ingest',
        component: () => import('../views/IngestView.vue'),
        meta: { title: '数据接入', breadcrumb: ['运行与研发', '数据接入'] },
      },
      {
        path: 'platform/semantics',
        name: 'semantics',
        component: () => import('../views/SemanticView.vue'),
        meta: { title: '语义中心', breadcrumb: ['运行与研发', '语义中心'] },
      },
      {
        path: 'platform',
        redirect: '/applications',
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
  document.title = `${to.meta.title || 'AI Hub'} · AI Hub`
})

export default router
