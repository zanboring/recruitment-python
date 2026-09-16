<template>
  <div class="card-header">
    <el-avatar class="logo-avatar" :size="40">
      <el-icon class="logo-icon"><ChatDotRound /></el-icon>
    </el-avatar>
    <div class="header-content">
      <h2 class="header-title">AI智能分析</h2>
      <p class="header-subtitle">基于大模型的招聘数据分析助手</p>
    </div>
    <div class="header-actions">
      <el-tooltip content="使用AI进行招聘相关分析" placement="left">
        <el-button 
          link 
          icon="InfoFilled" 
          class="info-button"
        />
      </el-tooltip>
      <el-tooltip content="关于" placement="left">
        <el-button 
          link 
          icon="HelpFilled" 
          class="help-button"
          @click="showAbout"
        />
      </el-tooltip>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ChatDotRound } from '@element-plus/icons-vue';

// Emits
const emit = defineEmits<{
  (e: 'showAbout'): void;
}>();

const showAbout = () => {
  emit('showAbout');
};
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 24px;
  background: linear-gradient(90deg, #409eff 0%, #667eea 100%);
  color: white;
  border-bottom: none;
  position: relative;
  overflow: hidden;
}

.card-header::before {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: 200px;
  height: 200px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 50%;
  transform: translate(50%, -50%);
}

.logo-avatar {
  background: rgba(255, 255, 255, 0.2);
  border: 2px solid rgba(255, 255, 255, 0.3);
  flex-shrink: 0;
}

.logo-icon {
  font-size: 24px;
  color: white;
}

.header-content {
  flex: 1;
}

.header-title {
  font-size: 24px;
  font-weight: bold;
  margin: 0 0 4px 0;
  letter-spacing: 1px;
}

.header-subtitle {
  font-size: 14px;
  opacity: 0.9;
  margin: 0;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.info-button,
.help-button {
  color: white;
  opacity: 0.8;
  transition: all 0.3s;
}

.info-button:hover,
.help-button:hover {
  opacity: 1;
  transform: scale(1.1);
}
</style>
