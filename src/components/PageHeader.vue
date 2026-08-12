<script setup>
defineProps({
  eyebrow: { type: String, default: '' },
  title: { type: String, required: true },
  description: { type: String, default: '' },
})
</script>

<template>
  <header class="page-header">
    <div v-if="$slots.tabs" class="page-header__tabs-row">
      <div class="page-header__tabs">
        <slot name="tabs" />
      </div>
    </div>
    <div v-if="$slots.default || $slots.actions" class="page-header__filters-row">
      <div v-if="$slots.default" class="page-header__filters">
        <slot />
      </div>
      <div v-if="$slots.actions" class="page-header__actions">
        <slot name="actions" />
      </div>
    </div>
  </header>
</template>

<style scoped>
.page-header {
  display: block;
  width: calc(100% + (var(--content-gutter) * 2));
  margin-left: calc(var(--content-gutter) * -1);
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}

.page-header__tabs-row {
  padding: 0 var(--header-gutter);
  border-bottom: 1px solid var(--line);
}

.page-header__tabs {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  width: 100%;
  min-width: 0;
  gap: 0 24px;
}

.page-header__filters-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  padding: 8px var(--header-gutter);
}

.page-header__actions {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  align-self: center;
  gap: 10px;
}

.page-header__filters {
  display: flex;
  flex: 1 1 320px;
  align-items: center;
  flex-wrap: wrap;
  min-width: 0;
  gap: 10px;
}

@media (max-width: 700px) {
  .page-header__filters-row {
    align-items: stretch;
    flex-direction: column;
  }

  .page-header__filters,
  .page-header__actions {
    width: 100%;
    flex-wrap: wrap;
  }

  .page-header__actions {
    justify-content: flex-start;
  }
}
</style>
