<template>
  <el-row :gutter="20" style="margin-top: 20px" v-if="quickCrawling || crawlLogs.length > 0">
    <el-col :span="24">
      <el-card class="log-card" shadow="never">
        <template #header>
          <div class="log-header">
            <div class="title">
              <el-icon><Document /></el-icon>
              <span>实时爬取日志</span>
            </div>
            <div class="log-toolbar">
              <!-- 日志统计 -->
              <div class="log-stats" v-if="crawlLogs.length > 0">
                <el-badge :value="logStats.info" type="info" class="stat-badge">
                  <el-tag type="info" effect="plain" size="small">INFO</el-tag>
                </el-badge>
                <el-badge :value="logStats.success" type="success" class="stat-badge">
                  <el-tag type="success" effect="plain" size="small">成功</el-tag>
                </el-badge>
                <el-badge :value="logStats.warning" type="warning" class="stat-badge">
                  <el-tag type="warning" effect="plain" size="small">警告</el-tag>
                </el-badge>
                <el-badge :value="logStats.error" type="danger" class="stat-badge">
                  <el-tag type="danger" effect="plain" size="small">错误</el-tag>
                </el-badge>
                <span class="log-total">共 {{ crawlLogs.length }} 条</span>
              </div>
              <!-- 日志过滤 -->
              <el-radio-group v-model="logFilter" size="small" @change="onLogFilterChange">
                <el-radio-button value="all">全部</el-radio-button>
                <el-radio-button value="info">信息</el-radio-button>
                <el-radio-button value="success">成功</el-radio-button>
                <el-radio-button value="error">错误</el-radio-button>
              </el-radio-group>
              <el-button type="danger" size="small" @click="clearLogs" :disabled="crawlLogs.length === 0">
                <el-icon><Delete /></el-icon>
                清空日志
              </el-button>
            </div>
          </div>
        </template>
        <div class="log-content" ref="logContentRef">
          <div v-for="(log, index) in filteredLogs" :key="index" class="log-item" :class="log.type">
            <span class="log-icon">
              <el-icon v-if="log.type === 'success'"><CircleCheck /></el-icon>
              <el-icon v-else-if="log.type === 'error'"><CircleClose /></el-icon>
              <el-icon v-else-if="log.type === 'warning'"><Warning /></el-icon>
              <el-icon v-else><InfoFilled /></el-icon>
            </span>
            <span class="log-time">{{ log.time }}</span>
            <span class="log-message">{{ log.message }}</span>
          </div>
          <div v-if="filteredLogs.length === 0 && crawlLogs.length > 0" class="log-empty">
            没有匹配 {{ logFilter === 'all' ? '' : logFilter.toUpperCase() }} 类型的日志
          </div>
          <div v-if="crawlLogs.length === 0" class="log-empty">
            暂无日志记录，执行爬取任务后将在此显示实时日志
          </div>
        </div>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { ElMessage } from 'element-plus';
import {
  Document,
  Delete,
  InfoFilled,
  CircleCheck,
  CircleClose,
  Warning
} from '@element-plus/icons-vue';

interface LogItem {
  type: 'info' | 'success' | 'warning' | 'error';
  time: string;
  message: string;
}

// Props
const props = defineProps<{
  crawlLogs: LogItem[];
  quickCrawling: boolean;
}>();

// Emits
const emit = defineEmits<{
  (e: 'clearLogs'): void;
}>();

// 日志过滤
const logFilter = ref<string>('all');

// 日志统计
const logStats = computed(() => {
  const stats = { info: 0, success: 0, warning: 0, error: 0 };
  for (const log of props.crawlLogs) {
    if (log.type === 'info') stats.info++;
    else if (log.type === 'success') stats.success++;
    else if (log.type === 'warning') stats.warning++;
    else if (log.type === 'error') stats.error++;
  }
  return stats;
});

// 日志过滤
const filteredLogs = computed(() => {
  if (logFilter.value === 'all') return props.crawlLogs;
  return props.crawlLogs.filter(log => log.type === logFilter.value);
});

// 日志内容引用
const logContentRef = ref<HTMLElement | null>(null);

const onLogFilterChange = () => {
  if (logContentRef.value) {
    logContentRef.value.scrollTop = 0;
  }
};

const clearLogs = () => {
  emit('clearLogs');
  ElMessage.success('日志已清空');
};

// 暴露方法给父组件
defineExpose({
  logContentRef
});
</script>

<style scoped>
.log-card {
  border: none;
  border-radius: 12px;
  background: #1a1a2e;
}

.log-card :deep(.el-card__header) {
  background: #16213e;
  border-bottom: 1px solid #0f3460;
  padding: 16px 20px;
}

.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.log-header .title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #e94560;
}

.log-content {
  background: #0f0f23;
  border-radius: 8px;
  padding: 16px;
  height: 400px;
  overflow-y: auto;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
}

.log-content::-webkit-scrollbar {
  width: 8px;
}

.log-content::-webkit-scrollbar-track {
  background: #1a1a2e;
  border-radius: 4px;
}

.log-content::-webkit-scrollbar-thumb {
  background: #e94560;
  border-radius: 4px;
}

.log-content::-webkit-scrollbar-thumb:hover {
  background: #ff6b6b;
}

.log-item {
  display: flex;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #1a1a2e;
  color: #e0e0e0;
}

.log-item:last-child {
  border-bottom: none;
}

.log-time {
  color: #888;
  font-size: 12px;
  min-width: 70px;
}

.log-message {
  flex: 1;
  word-break: break-all;
}

.log-item.info .log-message {
  color: #4a9eff;
}

.log-item.success .log-message {
  color: #4ade80;
}

.log-item.error .log-message {
  color: #f87171;
}

.log-empty {
  text-align: center;
  color: #666;
  padding: 40px 0;
  font-size: 14px;
}

.log-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.log-stats {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stat-badge {
  margin-right: 4px;
}

.stat-badge :deep(.el-badge__content) {
  font-size: 10px;
}

.log-total {
  color: #888;
  font-size: 12px;
  margin-left: 8px;
  padding-left: 12px;
  border-left: 1px solid #0f3460;
}

.log-icon {
  display: flex;
  align-items: center;
  min-width: 16px;
  margin-top: 2px;
}

.log-icon .el-icon {
  font-size: 14px;
}

.log-item.info .log-icon {
  color: #4a9eff;
}

.log-item.success .log-icon {
  color: #4ade80;
}

.log-item.error .log-icon {
  color: #f87171;
}

.log-item.warning .log-message {
  color: #fbbf24;
}
</style>
