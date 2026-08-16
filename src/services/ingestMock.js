// 原型期内存态 Mock；接后端后替换为 platformApi 调用。

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

const state = {
  policy: {
    retention_keep_versions: 100,
    retention_keep_days: null,
    payload_max_bytes: 1048576,
    page_limit_default: 200,
    page_limit_max: 5000,
    scheduled_reconcile_enabled: false,
    reconcile_interval_hours: 24,
  },
  sources: [
    {
      source_application_id: 'standalone-example',
      object_type: 'example_record',
      export_base_url: 'http://standalone-app:8100',
      interval_seconds: 60,
      lookback_versions: 100,
      page_limit: 200,
      enabled: false,
      last_cursor: 128,
      last_sync_at: null,
    },
    {
      source_application_id: 'order-center',
      object_type: 'order',
      export_base_url: 'https://order-center.internal',
      interval_seconds: 300,
      lookback_versions: 50,
      page_limit: 500,
      enabled: true,
      last_cursor: 40211,
      last_sync_at: new Date(Date.now() - 120_000).toISOString(),
    },
  ],
  runs: [
    {
      run_id: 'demo-run-01',
      action: 'reconcile',
      mode: '-',
      summary: 'drift=0 · compared=2 源',
      at: new Date(Date.now() - 3600_000).toISOString(),
    },
  ],
}

let seq = 2

function delay(ms = 160) {
  return new Promise((resolve) => { setTimeout(resolve, ms) })
}

export async function mockIngestGetConfig() {
  await delay()
  return clone({ policy: state.policy, sources: state.sources })
}

export async function mockIngestSavePolicy(next) {
  await delay()
  state.policy = clone(next)
  return clone(state.policy)
}

export async function mockIngestSaveSource(next) {
  await delay()
  const index = state.sources.findIndex(
    (item) => item.source_application_id === next.source_application_id
      && item.object_type === next.object_type,
  )
  if (index >= 0) state.sources[index] = clone(next)
  else state.sources.push(clone(next))
  return clone(next)
}

export async function mockIngestSetSourceEnabled(key, enabled) {
  await delay()
  const hit = state.sources.find(
    (item) => `${item.source_application_id}:${item.object_type}` === key,
  )
  if (hit) hit.enabled = enabled
  return clone(hit)
}

function pushRun(entry) {
  seq += 1
  state.runs.unshift({
    run_id: `demo-run-${String(seq).padStart(2, '0')}`,
    at: new Date().toISOString(),
    ...entry,
  })
}

export async function mockIngestRunAction(payload) {
  await delay(260)
  const { action } = payload
  if (action === 'sync') {
    pushRun({ action: 'sync', mode: payload.mode, summary: `已提交 ${payload.mode} 同步（模拟）` })
    return { status: 'accepted', message: '同步任务已提交（原型：未真正执行）' }
  }
  if (action === 'reconcile') {
    pushRun({ action: 'reconcile', mode: '-', summary: 'drift=1 · order 缺 3 条（模拟）' })
    return {
      status: 'ok',
      drift: [
        {
          source_application_id: 'order-center',
          object_type: 'order',
          missing_in_ods: 3,
          extra_in_ods: 0,
          compared_at: new Date().toISOString(),
        },
      ],
    }
  }
  if (action === 'rebuild') {
    pushRun({ action: 'rebuild', mode: payload.mode, summary: `重建 ${payload.mode}（模拟）` })
    return { status: 'accepted', message: `重建任务已提交（${payload.mode}，原型：未真正执行）` }
  }
  if (action === 'prune') {
    const dry = payload.dry_run !== false
    pushRun({ action: 'prune', mode: dry ? 'dry-run' : 'apply', summary: dry ? '将清理 42 个旧版本（模拟）' : '已清理 42 个旧版本（模拟）' })
    return { status: 'ok', dry_run: dry, would_delete: 42 }
  }
  return { status: 'error', message: '未知动作' }
}

export async function mockIngestListRuns() {
  await delay()
  return clone(state.runs)
}
