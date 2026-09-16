<template>
  <div class="model-switch-container" v-if="store.showSwitchButton">
    <el-dropdown trigger="click" @command="handleSwitch">
      <div class="switch-button">
        <span class="model-indicator" :class="currentIndicatorClass"></span>
        <span class="model-name">{{ store.currentModelName }}</span>
        <el-icon class="arrow-icon"><ArrowDown /></el-icon>
      </div>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item 
            command="primary" 
            :disabled="!store.zhipuAvailable"
          >
            <span class="option-icon primary"></span>
            <span>主API (智谱)</span>
            <span v-if="!store.zhipuAvailable" class="disabled-hint">(不可用)</span>
          </el-dropdown-item>
          <el-dropdown-item 
            command="local" 
            :disabled="!store.ollamaAvailable"
          >
            <span class="option-icon local"></span>
            <span>本地模型</span>
            <span v-if="store.ollamaAvailable" class="version-hint">{{ store.ollamaModel }}</span>
          </el-dropdown-item>
          <el-dropdown-item command="auto">
            <span class="option-icon auto"></span>
            <span>自动切换</span>
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
    
    <div v-if="showStatusTip" class="status-tip">
      <el-tooltip :content="statusTipContent" placement="bottom">
        <el-icon class="status-icon"><InfoFilled /></el-icon>
      </el-tooltip>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { ArrowDown, InfoFilled } from '@element-plus/icons-vue';
import { useModelStore } from '@/store/model';

const store = useModelStore();

const currentIndicatorClass = computed(() => {
  return `indicator-${store.currentModel}`;
});

const showStatusTip = computed(() => {
  return store.currentModel === 'auto';
});

const statusTipContent = computed(() => {
  if (store.ollamaAvailable && store.zhipuAvailable) {
    return '自动模式：优先使用本地模型，失败时回退到主API';
  } else if (store.ollamaAvailable) {
    return '自动模式：仅本地模型可用';
  } else if (store.zhipuAvailable) {
    return '自动模式：仅主API可用';
  }
  return '自动模式：所有服务均不可用';
});

function handleSwitch(command: string) {
  switch (command) {
    case 'primary':
      store.switchToPrimary();
      break;
    case 'local':
      store.switchToLocal();
      break;
    case 'auto':
      store.switchToAuto();
      break;
  }
}
</script>

<style scoped>
.model-switch-container {
  display: flex;
  align-items: center;
  gap: 8px;
}

.switch-button {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 20px;
  cursor: pointer;
  transition: all 0.3s ease;
  font-size: 14px;
}

.switch-button:hover {
  border-color: #667eea;
  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.2);
}

.model-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  transition: background-color 0.3s ease;
}

.indicator-primary {
  background-color: #667eea;
}

.indicator-local {
  background-color: #10b981;
}

.indicator-auto {
  background-color: #f59e0b;
}

.model-name {
  font-weight: 500;
  color: #333;
}

.arrow-icon {
  font-size: 14px;
  color: #909399;
}

.status-tip {
  color: #909399;
}

.status-icon {
  font-size: 16px;
  cursor: help;
}

:deep(.el-dropdown-menu) {
  min-width: 180px;
}

:deep(.el-dropdown-item) {
  display: flex;
  align-items: center;
  gap: 8px;
}

.option-icon {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.option-icon.primary {
  background-color: #667eea;
}

.option-icon.local {
  background-color: #10b981;
}

.option-icon.auto {
  background-color: #f59e0b;
}

.disabled-hint {
  color: #909399;
  font-size: 12px;
  margin-left: auto;
}

.version-hint {
  color: #10b981;
  font-size: 12px;
  margin-left: auto;
}
</style>
