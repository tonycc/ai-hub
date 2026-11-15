export const platformCapabilityGroups = [{
  key: 'platform', name: '平台能力', items: [
    { code: 'IDENTITY', name: '用户与组织', description: '企业身份、组织与平台管理权限', route: '/platform/identity' },
    { code: 'APPLICATIONS', name: '应用接入', description: '应用登记、凭据、接入检查与文档', route: '/applications' },
    { code: 'INGEST', name: '数据接入', description: '契约化数据汇聚、对账与重建', route: '/platform/ingest' },
    { code: 'AUDIT', name: '审计', description: '平台操作审计', route: '/platform/audit' },
    { code: 'OPERATIONS', name: '运维', description: '运行状态与部署配置', route: '/platform/operations' },
  ],
}]
