<template>
  <el-dialog
    v-model="visible"
    title="任务日志"
    width="600px"
  >
    <div class="task-log">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="任务ID">{{ task?.id }}</el-descriptions-item>
        <el-descriptions-item label="来源网站">{{ task?.sourceSite }}</el-descriptions-item>
        <el-descriptions-item label="关键词">{{ task?.keyword }}</el-descriptions-item>
        <el-descriptions-item label="城市">{{ task?.city }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="getStatusType(task?.status)">
            {{ getStatusText(task?.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="岗位数量">{{ task?.jobCount }}</el-descriptions-item>
        <el-descriptions-item label="消息">{{ task?.message }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ task?.createdAt }}</el-descriptions-item>
        <el-descriptions-item label="完成时间">{{ task?.finishedAt || '-' }}</el-descriptions-item>
      </el-descriptions>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
// Props & Emits
const visible = defineModel<boolean>('visible', { default: false });

interface Task {
  id: number;
  sourceSite: string;
  keyword: string;
  city: string;
  status: string;
  jobCount: number;
  createdAt: string;
  finishedAt?: string;
  message?: string;
}

defineProps<{
  task: Task | null;
}>();

const getStatusType = (status?: string) => {
  const types: Record<string, any> = {
    PENDING: 'info',
    RUNNING: 'warning',
    FINISHED: 'success',
    FAILED: 'danger'
  };
  return types[status || ''] || 'info';
};

const getStatusText = (status?: string) => {
  const texts: Record<string, string> = {
    PENDING: '待执行',
    RUNNING: '执行中',
    FINISHED: '已完成',
    FAILED: '失败'
  };
  return texts[status || ''] || status || '';
};
</script>

<style scoped>
.task-log {
  padding: 10px 0;
}
</style>
