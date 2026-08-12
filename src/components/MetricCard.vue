<script setup>
defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], required: true },
  unit: { type: String, default: '' },
  hint: { type: String, default: '' },
  icon: { type: String, default: 'DataLine' },
  tone: { type: String, default: 'slate' },
})
</script>

<template>
  <div class="metric-card" :class="`metric-card--${tone}`">
    <div class="metric-card__icon">
      <el-icon><component :is="icon" /></el-icon>
    </div>
    <div class="metric-card__content">
      <span class="metric-card__label">{{ label }}</span>
      <div class="metric-card__value-row">
        <strong>{{ value }}</strong><span v-if="unit">{{ unit }}</span>
      </div>
      <span v-if="hint" class="metric-card__hint">{{ hint }}</span>
    </div>
  </div>
</template>

<style scoped>
.metric-card {
  position: relative;
  display: flex;
  min-width: 0;
  gap: 13px;
  padding: 17px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
}

.metric-card::after {
  position: absolute;
  top: 0;
  right: 0;
  width: 46px;
  height: 3px;
  background: var(--tone, #6d7f8d);
  content: "";
}

.metric-card--orange {
  --tone: #d05d29;
}

.metric-card--blue {
  --tone: #3f7694;
}

.metric-card--green {
  --tone: #3d8067;
}

.metric-card--amber {
  --tone: #b77924;
}

.metric-card__icon {
  display: grid;
  flex: 0 0 38px;
  width: 38px;
  height: 38px;
  border-radius: 8px;
  color: var(--tone, #60717e);
  background: color-mix(in srgb, var(--tone, #60717e) 11%, white);
  font-size: 19px;
  place-items: center;
}

.metric-card__content {
  min-width: 0;
}

.metric-card__label {
  display: block;
  color: var(--ink-500);
  font-size: 13px;
}

.metric-card__value-row {
  display: flex;
  align-items: baseline;
  gap: 4px;
  margin-top: 5px;
}

.metric-card__value-row strong {
  color: var(--ink-900);
  font-size: 25px;
  font-weight: 720;
  letter-spacing: -0.04em;
  line-height: 1;
}

.metric-card__value-row span {
  color: var(--ink-500);
  font-size: 13px;
}

.metric-card__hint {
  display: block;
  margin-top: 6px;
  overflow: hidden;
  color: var(--ink-500);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
