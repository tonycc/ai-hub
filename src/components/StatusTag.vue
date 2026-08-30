<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: { type: String, required: true },
  label: { type: String, default: '' },
  size: { type: String, default: 'small' },
})

const map = {
  DRAFT: ['info', '草稿'],
  ACTIVE: ['success', '启用'],
  APPROVED: ['success', '已批准'],
  REJECTED: ['danger', '已拒绝'],
  DRAINING: ['warning', '过渡中'],
  DISABLED: ['info', '停用'],
  RETIRED: ['info', '已退役'],
  REVOKED: ['danger', '已撤销'],
  DEPRECATED: ['warning', '已弃用'],
  ERROR: ['danger', '异常'],
  RUNNING: ['primary', '运行中'],
  PASSED: ['success', '通过'],
  DENIED: ['danger', '拒绝'],
  SUCCESS: ['success', '成功'],
  DELIVERED: ['success', '已送达'],
  PENDING: ['warning', '待处理'],
  NOT_APPLICABLE: ['info', '不适用'],
  HEALTHY: ['success', '健康'],
  WARNING: ['warning', '警告'],
  CRITICAL: ['danger', '严重'],
  UNKNOWN: ['info', '未知'],
  VALIDATING: ['warning', '内部验证'],
  SUCCEEDED: ['success', '成功'],
  RETRYING: ['warning', '重试中'],
  FAILED: ['danger', '失败'],
  verified: ['success', '已验证'],
  reviewed: ['warning', '已评审'],
  draft: ['info', '草稿'],
  健康: ['success', '健康'],
  降级: ['warning', '降级'],
  已发布: ['success', '已发布'],
  已上线: ['success', '已上线'],
  正常: ['success', '正常'],
  成功: ['success', '成功'],
  已完成: ['success', '已完成'],
  已启用: ['success', '已启用'],
  已通过: ['success', '已通过'],
  已送达: ['success', '已送达'],
  通过: ['success', '通过'],
  规划中: ['info', '规划中'],
  已具备: ['success', '已具备'],
  内部验证: ['warning', '内部验证'],
  待配置: ['warning', '待配置'],
  待实施: ['info', '待实施'],
  未启用: ['info', '未启用'],
  待复核: ['warning', '待复核'],
  待处理: ['warning', '待处理'],
  待发布: ['warning', '待发布'],
  进行中: ['primary', '进行中'],
  处理中: ['warning', '处理中'],
  监控中: ['warning', '监控中'],
  部分失败: ['warning', '部分失败'],
  受控: ['warning', '受控'],
  已归档: ['info', '已归档'],
  完整: ['success', '完整'],
  失败: ['danger', '失败'],
}

const config = computed(() => map[props.status] || ['info', props.status])
</script>

<template>
  <el-tag :type="config[0]" :size="size" effect="light" round>
    {{ label || config[1] }}
  </el-tag>
</template>
