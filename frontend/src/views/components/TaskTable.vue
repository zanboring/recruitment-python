<template>
  <el-row :gutter="20" style="margin-top: 20px">
    <el-col :span="24">
      <el-card class="task-card" shadow="never">
        <template #header>
          <div class="card-header">
            <div class="title">
              <el-icon><List /></el-icon>
              <span>爬虫任务列表</span>
            </div>
            <el-tag type="info" size="small">共 {{ tasks.length }} 个任务</el-tag>
          </div>
        </template>

        <el-table :data="tasks" v-loading="loading" stripe>
          <el-table-column type="index" label="序号" width="70" :index="indexMethod" />
          <el-table-column label="来源网站" width="120">
            <template #default="{ row }">
              {{ row.sourceSiteLabel || row.sourceSite }}
            </template>
          </el-table-column>
          <el-table-column prop="keyword" label="关键词" width="130" show-overflow-tooltip />
          <el-table-column prop="city" label="城市" width="140" show-overflow-tooltip />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="getStatusType(row.status)" effect="dark">
                {{ getStatusText(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <!-- 说明列：承载失败/跳过原因（如「请求的平台均未实现：zhaopin」）。
               此前该字段只存在于接口与类型声明里，界面从不展示 ——
               用户只看到一个红色「失败」，完全不知道原因，只能去翻后端日志。 -->
          <el-table-column label="说明" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.message" :class="{ 'cell-warn': row.status === 'FAILED' }">
                {{ row.message }}
              </span>
              <span v-else class="cell-muted">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="jobCount" label="岗位数量" width="90" />
          <el-table-column prop="createdAt" label="创建时间" width="165" />
          <el-table-column prop="finishedAt" label="完成时间" width="165" />
          <el-table-column label="操作" width="280" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'PENDING'"
                type="primary"
                size="small"
                @click="startTask(row.id)"
              >
                启动
              </el-button>
              <el-button
                v-if="row.status === 'FINISHED'"
                type="success"
                size="small"
                @click="restartTask(row)"
              >
                重新爬取
              </el-button>
              <el-button
                type="info"
                size="small"
                @click="viewTaskLog(row)"
              >
                日志
              </el-button>
              <el-button
                type="danger"
                size="small"
                @click="handleDeleteTask(row)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <el-empty v-if="tasks.length === 0 && !loading" description="暂无爬虫任务" />
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus';
import { List } from '@element-plus/icons-vue';
import http from '@/api/http';
import { deleteCrawlTask } from '@/api/crawl';

interface Task {
  id: number;
  sourceSite: string;
  /** 平台中文名（由后端提供，缺失时回退展示 sourceSite） */
  sourceSiteLabel?: string;
  keyword: string;
  city: string;
  status: string;
  jobCount: number;
  createdAt: string;
  finishedAt: string;
  /** 失败/跳过原因；后端在任务失败时会写明，界面需要展示 */
  message?: string;
}

// Props
defineProps<{
  tasks: Task[];
  loading: boolean;
}>();

// Emits
const emit = defineEmits<{
  (e: 'loadData'): void;
  (e: 'viewTaskLog', task: Task): void;
}>();

const getStatusType = (status: string) => {
  const types: Record<string, any> = {
    PENDING: 'info',
    RUNNING: 'warning',
    FINISHED: 'success',
    FAILED: 'danger'
  };
  return types[status] || 'info';
};

const getStatusText = (status: string) => {
  const texts: Record<string, string> = {
    PENDING: '待执行',
    RUNNING: '执行中',
    FINISHED: '已完成',
    FAILED: '失败'
  };
  return texts[status] || status;
};

const indexMethod = (index: number) => {
  return index + 1;
};

const startTask = async (id: number) => {
  try {
    await http.post(`/crawl/task/${id}/start`);
    ElMessage.success('任务已启动');
    emit('loadData');
  } catch (error) {
    ElMessage.error('任务启动失败');
  }
};

const handleDeleteTask = async (task: Task) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除任务"${task.sourceSite} - ${task.keyword}"吗？`,
      '删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      }
    );
    
    await deleteCrawlTask(task.id);
    ElMessage.success('任务已删除');
    emit('loadData');
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败');
    }
  }
};

const restartTask = async (task: Task) => {
  try {
    await ElMessageBox.confirm(
      `确定要重新爬取"${task.sourceSite} - ${task.keyword}"吗？`,
      '重新爬取确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'info',
      }
    );
    
    ElMessage.info('正在创建新的爬取任务...');
    
    const newTask = {
      sourceSite: task.sourceSite,
      keyword: task.keyword,
      city: task.city || '长沙'
    };
    
    const taskRes = await http.post('/crawl/task', newTask);
    const taskId = typeof taskRes === 'number' ? taskRes : (taskRes as any).id || taskRes;
    await http.post(`/crawl/task/${taskId}/start`);
    
    ElMessage.success('新爬取任务已创建并启动');
    emit('loadData');
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error('重新爬取失败');
    }
  }
};

const viewTaskLog = (task: Task) => {
  emit('viewTaskLog', task);
};
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header .title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #333;
}

/* 说明列：失败原因用警示色，让人一眼看出「这里有问题」，
   而不是把原因埋成和普通文字一样的灰色 */
.cell-warn {
  color: #e6a23c;
}

.cell-muted {
  color: #c0c4cc;
}
</style>
