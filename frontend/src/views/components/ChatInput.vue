<template>
  <div class="chat-input-area">
    <div class="input-container">
      <el-input
        v-model="inputMessage"
        :placeholder="'请输入您的问题，例如：分析长沙Java后端岗位的薪资水平'"
        :rows="3"
        type="textarea"
        @keyup.enter.exact="handleSend"
        :class="{ 'input-focused': inputFocused }"
        @focus="inputFocused = true"
        @blur="inputFocused = false"
        resize="none"
      />
      <div class="input-toolbar">
        <div class="toolbar-left">
          <el-tooltip content="按Enter发送" placement="top">
            <el-button 
              link 
              icon="ChatLineSquare"
              class="toolbar-button"
            />
          </el-tooltip>
          <el-tooltip content="清空输入" placement="top">
            <el-button 
              link 
              icon="Delete"
              class="toolbar-button"
              @click="inputMessage = ''"
            />
          </el-tooltip>
        </div>
        <div class="toolbar-right">
          <span class="input-stats">{{ inputMessage.length }} 字符</span>
        </div>
      </div>
    </div>
    <div class="input-actions">
      <el-button
        v-if="!loading"
        type="primary"
        @click="handleSend"
        :disabled="!inputMessage.trim()"
        class="send-button"
        :class="{ 'send-button-active': inputMessage.trim() }"
      >
        <el-icon class="send-icon"><ArrowRight /></el-icon>
        发送
      </el-button>
      <el-button
        v-else
        type="danger"
        @click="handleStop"
        class="stop-button"
      >
        <el-icon class="stop-icon"><VideoPause /></el-icon>
        停止
      </el-button>
      <el-button 
        @click="clearMessages"
        class="clear-button"
      >
        <el-icon class="clear-icon"><Delete /></el-icon>
        清空对话
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ArrowRight, Delete, VideoPause } from '@element-plus/icons-vue';

// Props
const props = defineProps<{
  loading: boolean;
}>();

// Emits
const emit = defineEmits<{
  (e: 'send', message: string): void;
  (e: 'clear'): void;
  (e: 'stop'): void;
}>();

// 双向绑定
const inputMessage = defineModel<string>('inputMessage', { default: '' });

// 输入框焦点状态
const inputFocused = ref(false);

const handleSend = () => {
  if (!inputMessage.value.trim()) return;
  emit('send', inputMessage.value.trim());
};

const handleStop = () => {
  emit('stop');
};

const clearMessages = () => {
  emit('clear');
};
</script>

<style scoped>
.chat-input-area {
  margin: 20px;
}

.input-container {
  background: #ffffff;
  border: 2px solid #e4e7ed;
  border-radius: 12px;
  overflow: hidden;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  position: relative;
}

.input-container:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  transform: translateY(-1px);
}

.input-container:focus-within {
  border-color: #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.2);
  transform: translateY(-1px);
}

.input-container :deep(.el-textarea) {
  margin-bottom: 0;
  border: none;
  border-radius: 0;
}

.input-container :deep(.el-textarea__inner) {
  min-height: 120px;
  font-size: 14px;
  line-height: 1.5;
  border: none;
  resize: none;
  padding: 16px;
  border-radius: 12px 12px 0 0;
  transition: all 0.3s;
}

.input-container :deep(.el-textarea__inner:focus) {
  box-shadow: none;
}

.input-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-top: 1px solid #ebeef5;
  background: #fafafa;
  transition: all 0.3s;
}

.toolbar-left {
  display: flex;
  gap: 8px;
}

.toolbar-button {
  color: #909399;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  border-radius: 4px;
  padding: 4px;
}

.toolbar-button:hover {
  color: #409eff;
  transform: scale(1.1);
  background: rgba(64, 158, 255, 0.1);
}

.toolbar-right .input-stats {
  font-size: 12px;
  color: #909399;
  transition: all 0.3s;
}

.toolbar-right .input-stats.typing {
  color: #409eff;
  font-weight: 500;
}

.input-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
}

.send-button {
  display: flex;
  align-items: center;
  gap: 8px;
  border-radius: 8px;
  padding: 12px 24px;
  font-weight: 500;
  transition: all 0.3s;
  background: #409eff;
  border: none;
  box-shadow: 0 2px 4px rgba(64, 158, 255, 0.2);
}

.send-button:hover:not(:disabled) {
  background: #66b1ff;
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(64, 158, 255, 0.3);
}

.send-button:active:not(:disabled) {
  transform: translateY(0);
}

.send-button.send-button-active {
  background: #667eea;
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
}

.send-icon {
  font-size: 16px;
}

.clear-button {
  display: flex;
  align-items: center;
  gap: 8px;
  border-radius: 8px;
  padding: 12px 24px;
  font-weight: 500;
  transition: all 0.3s;
  border: 1px solid #dcdfe6;
  background: #ffffff;
}

.clear-button:hover {
  background: #f5f7fa;
  border-color: #c0c4cc;
  transform: translateY(-2px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.clear-button:active {
  transform: translateY(0);
}

.clear-icon {
  font-size: 16px;
}

.stop-button {
  display: flex;
  align-items: center;
  gap: 8px;
  border-radius: 8px;
  padding: 12px 24px;
  font-weight: 500;
  transition: all 0.3s;
  background: #f56c6c;
  border: none;
  box-shadow: 0 2px 4px rgba(245, 108, 108, 0.2);
}

.stop-button:hover {
  background: #f78989;
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(245, 108, 108, 0.3);
}

.stop-button:active {
  transform: translateY(0);
}

.stop-icon {
  font-size: 16px;
}
</style>
