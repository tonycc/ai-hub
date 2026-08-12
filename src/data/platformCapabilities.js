export const platformCapabilityGroups = [
  {
    key: 'foundation',
    name: '平台基础与接入',
    description: '建立可独立部署、可验证、可恢复的平台接入骨架。',
    icon: 'Connection',
    color: '#416f86',
    items: [
      { code: 'M0-DEPLOY', name: '部署基线', description: '单 PostgreSQL 集群、逻辑隔离、部署档位和配置校验', route: '/platform/operations', phase: 'M0', status: '已完成' },
      { code: 'APP-REGISTRY', name: '应用注册', description: '登记环境、入口、回调、健康、版本和接入能力', route: '/applications', phase: 'V0.1', status: '已具备' },
      { code: 'PORTAL-APP', name: '平台门户', description: '按权限展示应用入口和平台治理入口', route: '/', phase: 'V0.1', status: '已具备' },
      { code: 'ACCESS-API', name: '平台 API 接入', description: '用户令牌、服务身份、scope、错误码和调用审计', route: '/platform/integrations', phase: 'V0.1', status: '已具备' },
      { code: 'EVENT-KIT', name: '按需事件接入', description: 'RabbitMQ、Outbox、Inbox、幂等和投影重建模板', route: '/platform/integrations', phase: 'V0.1', status: '进行中' },
    ],
  },
  {
    key: 'identity-security',
    name: '身份、权限与安全',
    description: '统一治理身份映射、组织、角色、权限点、数据范围和凭据。',
    icon: 'Lock',
    color: '#527a64',
    items: [
      { code: 'IAM-IDENTITY', name: '用户与组织', description: 'authentik 身份映射、组织和账号生命周期', route: '/platform/identity', phase: 'V0.5', status: '进行中' },
      { code: 'IAM-RBAC', name: '角色与权限点', description: '角色、权限注册、授权版本和访问复核', route: '/platform/permissions', phase: 'V0.5', status: '进行中' },
      { code: 'IAM-SCOPE', name: '数据范围', description: '应用级数据范围契约与本地对象级校验边界', route: '/platform/permissions', phase: 'V0.5', status: '进行中' },
      { code: 'APP-CREDENTIAL', name: '应用凭据', description: '服务身份、scope、轮换、撤销和紧急停用', route: '/platform/integrations', phase: 'V0.5', status: '进行中' },
      { code: 'AUDIT-SECURITY', name: '安全审计', description: '身份、授权、凭据和公共能力调用审计', route: '/platform/audit', phase: 'V0.5', status: '进行中' },
    ],
  },
  {
    key: 'public-services',
    name: '平台公共服务',
    description: '通过稳定契约向独立应用提供最小公共能力。',
    icon: 'Grid',
    color: '#836b48',
    items: [
      { code: 'MSG-NOTIFY', name: '消息通知', description: '通知请求、测试送达、状态查询和失败原因', route: '/platform/notifications', phase: 'V0.5', status: '进行中' },
      { code: 'XAPP-CATALOG', name: '契约目录', description: 'OpenAPI、AsyncAPI、能力、版本和消费方登记', route: '/platform/integrations', phase: 'V0.5', status: '已具备' },
      { code: 'PROJECTION', name: '只读投影', description: '字段白名单、版本水位、对账和从空库重建', route: '/platform/integrations', phase: 'V0.5', status: '待实施' },
      { code: 'PLATFORM-CONFIG', name: '平台配置', description: '平台功能开关、配额、保留策略和配置版本', route: '/platform/settings', phase: 'V1.0', status: '待实施' },
    ],
  },
  {
    key: 'operations-development',
    name: '运维与开发者体验',
    description: '保障平台可以发布、监控、恢复并被独立应用稳定消费。',
    icon: 'SetUp',
    color: '#58667b',
    items: [
      { code: 'OBS-CENTER', name: '运行监控', description: '健康、日志、指标、事件积压、配额和告警', route: '/platform/operations', phase: 'V1.0', status: '待实施' },
      { code: 'BACKUP-RECOVERY', name: '备份恢复', description: '备份、恢复演练、RPO、RTO 和运行手册', route: '/platform/operations', phase: 'V1.0', status: '待实施' },
      { code: 'DEV-CONTRACT', name: '开发者中心', description: '公开契约、SDK、沙箱、本地替身和接入文档', route: '/platform/developer', phase: 'V0.5', status: '进行中' },
      { code: 'CONFORMANCE', name: '接入认证', description: 'API-only 与按需事件配置的一致性测试', route: '/platform/developer', phase: 'V0.5', status: '已具备' },
    ],
  },
  {
    key: 'later-governance',
    name: '后续治理能力',
    description: '只有形成明确平台需求和验收标准后才启动。',
    icon: 'MagicStick',
    color: '#735f84',
    items: [
      { code: 'SEM-CENTER', name: '企业语义中心', description: '语义目录、版本、来源绑定和变更影响', route: '/semantics', phase: 'V1.1', status: '待实施' },
      { code: 'AI-GOVERNANCE', name: 'AI 治理中心', description: '模型、知识、工具、评测、证据和运行审计', route: '/ai-center', phase: 'V1.2', status: '待实施' },
      { code: 'OPTIONAL-WORKFLOW', name: '工作流与统一待办', description: '仅在正式纳入平台产品范围后独立立项', route: '/platform', phase: '后置', status: '未启用' },
      { code: 'OPTIONAL-FILE', name: '文件与通用表单', description: '仅在正式纳入平台产品范围后独立立项', route: '/platform', phase: '后置', status: '未启用' },
    ],
  },
]

export const platformServices = {
  identity: {
    title: '用户与组织',
    description: '管理平台身份映射、组织和账号生命周期；凭据与会话由 authentik 负责。',
    eyebrow: 'IDENTITY & ORGANIZATION',
    icon: 'UserFilled',
    tone: '#416f86',
    primaryAction: '同步用户',
    metrics: [
      { label: '身份映射', value: '3', unit: '条', hint: '仅原型数据', icon: 'User', tone: 'blue' },
      { label: '组织节点', value: '3', unit: '个', hint: '平台治理角色', icon: 'OfficeBuilding', tone: 'green' },
      { label: '待复核账号', value: '1', unit: '个', hint: '账号治理待 V0.5', icon: 'Warning', tone: 'amber' },
      { label: '授权版本', value: '1', unit: '版', hint: 'M1 缓存失效基线', icon: 'Refresh', tone: 'blue' },
    ],
    sections: [
      {
        key: 'users', label: '用户映射', action: '同步测试用户',
        columns: [
          { field: 'name', label: '显示名称', minWidth: 150 }, { field: 'account', label: '身份主体', minWidth: 190, type: 'mono' },
          { field: 'role', label: '平台角色', minWidth: 150 }, { field: 'source', label: '身份来源', width: 120 },
          { field: 'status', label: '状态', width: 100, type: 'status' }, { field: 'updatedAt', label: '最近同步', width: 130 },
        ],
        rows: [
          { name: '平台管理员', account: 'usr.platform-admin', role: '平台管理员', source: 'authentik', status: '待实施', updatedAt: '—' },
          { name: '应用开发者', account: 'usr.app-developer', role: '应用开发者', source: 'authentik', status: '待实施', updatedAt: '—' },
          { name: '安全审计员', account: 'usr.security-auditor', role: '安全审计', source: 'authentik', status: '待实施', updatedAt: '—' },
        ],
      },
      {
        key: 'organizations', label: '组织节点', action: '新增组织节点',
        columns: [
          { field: 'name', label: '组织名称', minWidth: 180 }, { field: 'code', label: '组织编码', minWidth: 150, type: 'mono' },
          { field: 'type', label: '类型', width: 110, type: 'tag' }, { field: 'owner', label: '负责人', minWidth: 140 },
          { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { name: '平台产品组', code: 'ORG-PLATFORM-PRODUCT', type: '团队', owner: '平台产品负责人', status: '待配置' },
          { name: '平台研发组', code: 'ORG-PLATFORM-DEV', type: '团队', owner: '平台技术负责人', status: '待配置' },
          { name: '平台运维组', code: 'ORG-PLATFORM-OPS', type: '团队', owner: '平台运行负责人', status: '待配置' },
        ],
      },
    ],
  },
  permissions: {
    title: '权限与安全',
    description: '治理角色、权限点、scope、数据范围和授权版本，应用仍执行对象级最终校验。',
    eyebrow: 'AUTHORIZATION & SECURITY',
    icon: 'Lock',
    tone: '#527a64',
    primaryAction: '创建角色',
    metrics: [
      { label: '平台角色', value: '4', unit: '个', hint: '目标角色模型', icon: 'UserFilled', tone: 'blue' },
      { label: '权限点', value: '2', unit: '项', hint: 'M1 中性契约', icon: 'Key', tone: 'green' },
      { label: 'scope 定义', value: '5', unit: '项', hint: 'M1 最小权限', icon: 'Filter', tone: 'amber' },
      { label: '异常授权', value: '0', unit: '项', hint: '运行时门禁通过', icon: 'CircleCheck', tone: 'green' },
    ],
    sections: [
      {
        key: 'roles', label: '角色管理', action: '创建角色',
        columns: [
          { field: 'name', label: '角色名称', minWidth: 170 }, { field: 'code', label: '角色编码', minWidth: 180, type: 'mono' },
          { field: 'scope', label: '作用范围', minWidth: 150 }, { field: 'permissions', label: '权限点', width: 90 },
          { field: 'owner', label: '责任方', minWidth: 140 }, { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { name: '平台管理员', code: 'platform_admin', scope: '平台管理端', permissions: 8, owner: '平台产品组', status: '待实施' },
          { name: '应用开发者', code: 'application_developer', scope: '指定应用', permissions: 5, owner: '平台研发组', status: '待实施' },
          { name: '安全审计员', code: 'security_auditor', scope: '全平台只读', permissions: 4, owner: '安全团队', status: '待实施' },
          { name: '平台运维员', code: 'platform_operator', scope: '运行环境', permissions: 5, owner: '平台运维组', status: '待实施' },
        ],
      },
      {
        key: 'permissions', label: '权限点目录', action: '登记权限点',
        columns: [
          { field: 'code', label: '权限编码', minWidth: 240, type: 'mono' }, { field: 'name', label: '权限名称', minWidth: 170 },
          { field: 'module', label: '平台模块', minWidth: 140 }, { field: 'risk', label: '风险', width: 90, type: 'tag' },
          { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { code: 'platform.application.read', name: '查看应用登记', module: '应用中心', risk: '低', status: '待实施' },
          { code: 'platform.application.manage', name: '管理应用登记', module: '应用中心', risk: '高', status: '待实施' },
          { code: 'platform.credential.rotate', name: '轮换应用凭据', module: '接入治理', risk: '高', status: '待实施' },
          { code: 'platform.audit.read', name: '查看平台审计', module: '审计中心', risk: '中', status: '待实施' },
        ],
      },
      {
        key: 'scopes', label: 'scope 与数据范围', action: '登记 scope',
        columns: [
          { field: 'name', label: '名称', minWidth: 180 }, { field: 'code', label: '编码', minWidth: 220, type: 'mono' },
          { field: 'appliesTo', label: '适用身份', minWidth: 150 }, { field: 'owner', label: '责任方', minWidth: 130 },
          { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { name: '读取当前用户', code: 'platform.me.read', appliesTo: '用户令牌', owner: '身份模块', status: '待实施' },
          { name: '请求测试通知', code: 'platform.notification.request', appliesTo: '服务身份', owner: '通知模块', status: '待实施' },
          { name: '发布示例事件', code: 'event.example.record.publish', appliesTo: '服务身份', owner: '事件接入', status: '待实施' },
        ],
      },
    ],
  },
  settings: {
    title: '平台配置',
    description: '集中管理平台级功能开关、配额和保留策略，不保存应用领域配置。',
    eyebrow: 'PLATFORM SETTINGS',
    icon: 'SetUp',
    tone: '#826846',
    primaryAction: '新增配置',
    metrics: [
      { label: '平台配置', value: '6', unit: '项', hint: '仅平台作用域', icon: 'Setting', tone: 'blue' },
      { label: '功能开关', value: '3', unit: '项', hint: '全部默认关闭', icon: 'Switch', tone: 'amber' },
      { label: '配额模板', value: '2', unit: '套', hint: '基础与标准事件', icon: 'DataAnalysis', tone: 'green' },
      { label: '待复核项', value: '2', unit: '项', hint: '生产参数未冻结', icon: 'Warning', tone: 'amber' },
    ],
    sections: [
      {
        key: 'flags', label: '功能开关', action: '新增开关',
        columns: [
          { field: 'name', label: '名称', minWidth: 180 }, { field: 'key', label: '配置键', minWidth: 250, type: 'mono' },
          { field: 'defaultValue', label: '默认值', width: 100 }, { field: 'scope', label: '作用域', minWidth: 140 },
          { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { name: '事件发布能力', key: 'integration.event_publisher.enabled', defaultValue: 'false', scope: '按应用', status: '未启用' },
          { name: '事件消费能力', key: 'integration.event_consumer.enabled', defaultValue: 'false', scope: '按应用', status: '未启用' },
          { name: '平台投影能力', key: 'integration.projection_source.enabled', defaultValue: 'false', scope: '按应用', status: '未启用' },
        ],
      },
      {
        key: 'quotas', label: '配额模板', action: '新增配额模板',
        columns: [
          { field: 'name', label: '模板', minWidth: 170 }, { field: 'profile', label: '部署档位', width: 130 },
          { field: 'apiRate', label: 'API 速率', width: 120 }, { field: 'eventRate', label: '事件速率', width: 120 },
          { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { name: '基础接入默认值', profile: '基础接入', apiRate: '待冻结', eventRate: '不适用', status: '待配置' },
          { name: '标准事件默认值', profile: '标准事件', apiRate: '待冻结', eventRate: '待冻结', status: '待配置' },
        ],
      },
      {
        key: 'retention', label: '保留策略', action: '新增保留策略',
        columns: [
          { field: 'dataType', label: '数据类型', minWidth: 170 }, { field: 'retention', label: '保留期', width: 130 },
          { field: 'archive', label: '归档方式', minWidth: 160 }, { field: 'owner', label: '责任方', minWidth: 130 },
          { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { dataType: '平台审计', retention: '待确认', archive: '追加写与归档', owner: '安全团队', status: '待配置' },
          { dataType: '事件死信', retention: '待确认', archive: '人工处置后归档', owner: '平台运维组', status: '待配置' },
        ],
      },
    ],
  },
  audit: {
    title: '审计中心',
    description: '查询平台配置、权限、凭据和公共能力调用记录。',
    eyebrow: 'AUDIT & COMPLIANCE',
    icon: 'Tickets',
    tone: '#735f84',
    primaryAction: '导出审计',
    metrics: [
      { label: '今日记录', value: '7', unit: '条', hint: '原型演示数据', icon: 'Document', tone: 'blue' },
      { label: '高风险变更', value: '1', unit: '条', hint: '等待复核', icon: 'Warning', tone: 'amber' },
      { label: '调用拒绝', value: '3', unit: '类', hint: '错误令牌、scope、撤销', icon: 'Lock', tone: 'green' },
      { label: '审计完整率', value: '100', unit: '%', hint: 'M1 门禁场景', icon: 'CircleCheck', tone: 'green' },
    ],
    sections: [
      {
        key: 'operations', label: '管理操作', action: '刷新审计',
        columns: [
          { field: 'requestId', label: 'Request ID', minWidth: 180, type: 'mono' }, { field: 'actor', label: '操作者', minWidth: 140 },
          { field: 'action', label: '动作', minWidth: 190 }, { field: 'target', label: '目标', minWidth: 190 },
          { field: 'time', label: '时间', width: 140 }, { field: 'status', label: '结果', width: 100, type: 'status' },
        ],
        rows: [
          { requestId: 'req-doc-scope-001', actor: '平台维护者', action: '收敛平台建设范围', target: '产品与实施文档', time: '今天', status: '成功' },
          { requestId: 'req-frontend-clean-001', actor: '平台维护者', action: '移除领域演示代码', target: '平台前端原型', time: '今天', status: '成功' },
          { requestId: 'req-m0-03-001', actor: '平台维护者', action: '验证数据库逻辑隔离', target: '本地部署基线', time: '今天', status: '成功' },
          { requestId: 'req-m0-04-001', actor: '平台维护者', action: '验证 Compose 部署档位', target: 'base-access / standard-events', time: '刚刚', status: '成功' },
          { requestId: 'req-m0-06-001', actor: '平台维护者', action: '验证平台数据库边界', target: 'platform_core / platform_projection', time: '刚刚', status: '成功' },
          { requestId: 'req-m0-07-001', actor: '平台维护者', action: '验证配置与密钥边界', target: 'Compose / Pydantic Settings', time: '刚刚', status: '成功' },
          { requestId: 'req-m0-08-001', actor: '平台维护者', action: '验证生产组件精确锁', target: '镜像摘要 / 两个部署档位 / 权限边界', time: '刚刚', status: '成功' },
          { requestId: 'req-m0-09-001', actor: '平台维护者', action: '建立基础 CI 门禁', target: 'GitHub Actions / Required gate / main 分支保护', time: '刚刚', status: '成功' },
          { requestId: 'req-m1-runtime-001', actor: '平台维护者', action: '验证身份与 API 纵向链路', target: 'OIDC / 权限 / 通知 / 审计 / 故障边界', time: '刚刚', status: '成功' },
        ],
      },
      {
        key: 'reviews', label: '访问复核', action: '发起复核',
        columns: [
          { field: 'subject', label: '复核对象', minWidth: 200 }, { field: 'reviewer', label: '复核人', minWidth: 150 },
          { field: 'due', label: '截止时间', width: 130 }, { field: 'findings', label: '待处理项', width: 100 },
          { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { subject: 'M1 中性用户权限', reviewer: '安全负责人', due: '已完成', findings: 0, status: '已完成' },
          { subject: '参考应用最小 scope', reviewer: '平台技术负责人', due: '已完成', findings: 0, status: '已完成' },
        ],
      },
    ],
  },
  operations: {
    title: '运维中心',
    description: '跟踪部署档位、服务健康、数据库边界、备份恢复和发布状态。',
    eyebrow: 'OPERATIONS & RELIABILITY',
    icon: 'Monitor',
    tone: '#4d6b73',
    primaryAction: '运行基线检查',
    metrics: [
      { label: '当前里程碑', value: 'M2', hint: '可靠事件纵向链路', icon: 'Flag', tone: 'blue' },
      { label: 'M1 检查', value: '10', unit: '项', hint: '运行时门禁通过', icon: 'CircleCheck', tone: 'green' },
      { label: 'PostgreSQL 服务', value: '1', unit: '个', hint: '三个逻辑库已隔离', icon: 'Coin', tone: 'green' },
      { label: '生产 SLO', value: '—', hint: 'V1.0 前确认', icon: 'DataLine', tone: 'blue' },
    ],
    sections: [
      {
        key: 'services', label: '运行组件', action: '刷新健康状态',
        columns: [
          { field: 'name', label: '组件', minWidth: 180 }, { field: 'profile', label: '部署档位', width: 130 },
          { field: 'endpoint', label: '本地入口', minWidth: 220, type: 'mono' }, { field: 'owner', label: '责任方', minWidth: 140 },
          { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { name: '平台 API', profile: '基础接入', endpoint: 'http://platform.localhost:8088/platform-api/v1', owner: '平台研发组', status: '已具备' },
          { name: '平台门户', profile: '基础接入', endpoint: 'http://platform.localhost:8088', owner: '平台研发组', status: '已具备' },
          { name: '接入参考应用', profile: '基础接入', endpoint: 'http://app.localhost:8088', owner: '接入工具', status: '已具备' },
          { name: 'RabbitMQ', profile: '标准事件', endpoint: 'amqp://localhost:5672', owner: '平台运维组', status: '已具备' },
          { name: 'authentik / Traefik', profile: '基础接入', endpoint: 'http://auth.localhost:8088', owner: '平台运维组', status: '已具备' },
        ],
      },
      {
        key: 'databases', label: '数据库边界', action: '运行权限检查',
        columns: [
          { field: 'database', label: '逻辑数据库', minWidth: 190, type: 'mono' }, { field: 'role', label: '运行角色', minWidth: 190, type: 'mono' },
          { field: 'migrationOwner', label: '迁移归属', minWidth: 170 }, { field: 'writeScope', label: '写入范围', minWidth: 210 },
          { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { database: 'platform_db', role: 'ai_hub_platform', migrationOwner: 'ai_hub_platform_migrator', writeScope: 'platform_core', status: '已具备' },
          { database: 'platform_db', role: 'ai_hub_projection', migrationOwner: 'ai_hub_projection_migrator', writeScope: 'platform_projection', status: '已具备' },
          { database: 'standalone_app_db', role: 'standalone_app', migrationOwner: '参考应用迁移', writeScope: 'app', status: '已具备' },
          { database: 'authentik_db', role: 'authentik', migrationOwner: 'authentik', writeScope: '身份内部表', status: '已具备' },
        ],
      },
      {
        key: 'recovery', label: '备份与恢复', action: '登记恢复演练',
        columns: [
          { field: 'resource', label: '资源', minWidth: 180 }, { field: 'strategy', label: '策略', minWidth: 220 },
          { field: 'rpo', label: 'RPO', width: 100 }, { field: 'rto', label: 'RTO', width: 100 },
          { field: 'owner', label: '责任方', minWidth: 140 }, { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { resource: '本地开发数据库', strategy: '可从迁移与初始化脚本重建', rpo: '不适用', rto: '待测量', owner: '平台研发组', status: '进行中' },
          { resource: '生产 PostgreSQL', strategy: '待 V1.0 冻结', rpo: '待确认', rto: '待确认', owner: '平台运维组', status: '待实施' },
        ],
      },
    ],
  },
  developer: {
    title: '开发者中心',
    description: '提供公开契约、Python SDK、沙箱、参考应用和接入认证结果。',
    eyebrow: 'DEVELOPER EXPERIENCE',
    icon: 'Tools',
    tone: '#58667b',
    primaryAction: '运行接入认证',
    metrics: [
      { label: 'OpenAPI', value: '1', unit: '份', hint: 'M1 平台 API 契约', icon: 'Document', tone: 'blue' },
      { label: 'AsyncAPI', value: '1', unit: '份', hint: '示例事件骨架', icon: 'Connection', tone: 'green' },
      { label: 'Python SDK', value: '1', unit: '个', hint: 'workspace 版本', icon: 'Box', tone: 'amber' },
      { label: '认证测试', value: '10', unit: '项', hint: 'M1 运行时门禁通过', icon: 'CircleCheck', tone: 'green' },
    ],
    sections: [
      {
        key: 'contracts', label: '公开契约', action: '校验契约',
        columns: [
          { field: 'name', label: '契约', minWidth: 190 }, { field: 'path', label: '文件', minWidth: 300, type: 'mono' },
          { field: 'version', label: '版本', width: 100 }, { field: 'owner', label: '责任方', minWidth: 140 },
          { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { name: '平台公共 API', path: 'contracts/api/platform-api.openapi.yaml', version: 'v0.2.0', owner: '平台研发组', status: '已具备' },
          { name: '平台事件契约', path: 'contracts/events/ai-hub.asyncapi.yaml', version: 'v1-draft', owner: '平台研发组', status: '已具备' },
        ],
      },
      {
        key: 'sdk', label: 'SDK 与参考', action: '查看接入示例',
        columns: [
          { field: 'name', label: '制品', minWidth: 190 }, { field: 'location', label: '位置', minWidth: 280, type: 'mono' },
          { field: 'purpose', label: '用途', minWidth: 260 }, { field: 'status', label: '状态', width: 100, type: 'status' },
        ],
        rows: [
          { name: 'Python 接入 SDK', location: 'sdk/python', purpose: 'OIDC、授权缓存、API 客户端与事件信封', status: '已具备' },
          { name: '中性参考应用', location: 'examples/standalone-app', purpose: 'API-only 接入与故障边界认证', status: '已具备' },
        ],
      },
      {
        key: 'checks', label: '接入认证', action: '运行全部检查',
        columns: [
          { field: 'name', label: '检查项', minWidth: 220 }, { field: 'scope', label: '范围', minWidth: 240 },
          { field: 'lastRun', label: '最近运行', width: 140 }, { field: 'findings', label: '问题数', width: 90 },
          { field: 'status', label: '结果', width: 100, type: 'status' },
        ],
        rows: [
          { name: 'Python 单元测试', scope: '平台、SDK、参考应用', lastRun: 'M1 本轮', findings: 0, status: '通过' },
          { name: 'Python 静态检查', scope: 'Ruff 与 Pyright strict', lastRun: 'M1 本轮', findings: 0, status: '通过' },
          { name: '模块边界检查', scope: '平台后端模块', lastRun: 'M1 本轮', findings: 0, status: '通过' },
          { name: '前端生产构建', scope: 'npm ci 与平台管理端制品', lastRun: 'M1 本轮', findings: 0, status: '通过' },
          { name: '组件锁一致性', scope: '清单、Compose、Dockerfile 与环境模板', lastRun: 'M1 本轮', findings: 0, status: '通过' },
          { name: '精确镜像运行门禁', scope: '身份、API、迁移、健康与权限边界', lastRun: 'M1 本轮', findings: 0, status: '通过' },
          { name: '公开契约与 CI 自检', scope: 'OpenAPI、AsyncAPI、CloudEvents 与工作流', lastRun: 'M1 本轮', findings: 0, status: '通过' },
          { name: 'M1 运行时纵向门禁', scope: 'OIDC、授权、通知、审计、降级与独立重启', lastRun: 'M1 本轮', findings: 0, status: '通过' },
          { name: '远端 GitHub Actions', scope: 'M0 三个作业与 Required gate', lastRun: '运行 31557248062', findings: 0, status: '通过' },
          { name: 'main 分支保护', scope: 'Pull Request 与 Required gate 必需检查', lastRun: 'M0-09 本轮', findings: 0, status: '通过' },
        ],
      },
    ],
  },
}

export const messageItems = [
  { id: 'msg-1', category: '实施进度', title: 'M1 身份与 API 已完成', summary: '全新环境已通过 OIDC、权限、服务身份、通知、审计、故障降级和独立重启门禁。', app: '平台实施', time: '刚刚', unread: true, icon: 'CircleCheck', tone: 'success', route: '/platform/operations', context: 'M1-10 · completed' },
  { id: 'msg-2', category: '安全提醒', title: '本地示例密码不得用于生产环境', summary: '生产凭据需要进入密钥管理并完成轮换、撤销与泄露响应演练。', app: '安全治理', time: '今天', unread: true, icon: 'Lock', tone: 'danger', route: '/platform/settings', context: 'SEC-CONFIG · 密钥与配置边界' },
  { id: 'msg-3', category: '接入结果', title: '中性参考应用 API-only 认证通过', summary: 'OIDC、权限、对象级拒绝、服务身份通知和故障边界均已验证；事件接入进入 M2。', app: '开发者中心', time: '今天', unread: false, icon: 'CircleCheck', tone: 'success', route: '/platform/developer', context: 'standalone-reference · API_CLIENT' },
  { id: 'msg-4', category: '平台公告', title: '平台建设范围已收敛', summary: '真实业务应用不属于平台交付物，历史领域演示代码已从平台制品中移除。', app: '平台产品', time: '今天', unread: false, icon: 'Bell', tone: 'info', route: '/', context: 'PLATFORM-SCOPE · 平台公共能力' },
]
