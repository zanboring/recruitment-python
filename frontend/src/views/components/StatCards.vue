<template>
  <el-row :gutter="20" style="margin-top: 20px">
    <el-col :span="6">
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon">
            <el-icon><DataAnalysis /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">{{ totalJobs }}</div>
            <div class="stat-label">总岗位数</div>
          </div>
        </div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon">
            <el-icon><CircleCheck /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">{{ activeJobs }}</div>
            <div class="stat-label">有效岗位</div>
          </div>
        </div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon">
            <el-icon><Warning /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">{{ offlineJobs }}</div>
            <div class="stat-label">已下架</div>
          </div>
        </div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon">
            <el-icon><Timer /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">{{ totalTasks }}</div>
            <div class="stat-label">爬取任务</div>
          </div>
        </div>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import {
  DataAnalysis,
  CircleCheck,
  Warning,
  Timer
} from '@element-plus/icons-vue';

// Props
defineProps<{
  totalJobs: number;
  activeJobs: number;
  offlineJobs: number;
  totalTasks: number;
}>();
</script>

<style scoped>
.stat-card {
  border: none !important;
  border-radius: 16px !important;
  background: linear-gradient(135deg, #ffffff 0%, #f8f9fc 100%) !important;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06) !important;
  transition: all 0.3s ease !important;
}

.stat-card:hover {
  transform: translateY(-4px) !important;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.12) !important;
}

.stat-content {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 0;
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  color: #fff;
}

.stat-card:nth-child(1) .stat-icon {
  background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%);
}

.stat-card:nth-child(2) .stat-icon {
  background: linear-gradient(135deg, #10b981 0%, #34d399 100%);
}

.stat-card:nth-child(3) .stat-icon {
  background: linear-gradient(135deg, #f59e0b 0%, #fbbf24 100%);
}

.stat-card:nth-child(4) .stat-icon {
  background: linear-gradient(135deg, #ef4444 0%, #f87171 100%);
}

.stat-icon :deep(.el-icon) {
  color: #fff !important;
}

.stat-info {
  flex: 1;
}

.stat-value {
  font-size: 24px;
  font-weight: 800;
  color: #303133;
  line-height: 1.2;
}

.stat-label {
  font-size: 14px;
  color: #909399;
  margin-top: 4px;
}
</style>
