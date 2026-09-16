<template>
  <el-row :gutter="20" style="margin-top: 20px">
    <el-col :span="24">
      <el-card class="progress-card" shadow="never">
        <div class="progress-header">
          <el-icon><Loading /></el-icon>
          <span>爬取进度</span>
        </div>
        <el-progress :percentage="crawlProgress" :status="crawlProgress === 100 ? 'success' : 'warning'" />
        <div class="progress-info">
          <span>当前状态: {{ crawlStatus }}</span>
        </div>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { Loading } from '@element-plus/icons-vue';

// Props
defineProps<{
  crawlProgress: number;
  crawlStatus: string;
}>();
</script>

<style scoped>
.progress-card {
  border: none !important;
  border-radius: 16px !important;
  background: #fff !important;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06) !important;
}

.progress-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 16px;
}

.progress-info {
  margin-top: 12px;
  font-size: 14px;
  color: #606266;
  text-align: center;
}
</style>
