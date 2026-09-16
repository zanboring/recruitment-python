<template>
  <div class="settings-page">
    <el-row :gutter="20">
      <el-col :span="16">
        <!-- 爬虫配置卡片 -->
        <el-card class="setting-card" shadow="never">
          <template #header>
            <div class="card-header">
              <div class="title">
                <el-icon><Setting /></el-icon>
                <span>爬虫任务设置</span>
              </div>
            </div>
          </template>

          <el-form :model="form" label-width="100px" style="margin-top: 16px" size="large">
            <el-form-item label="数据来源">
              <el-checkbox-group v-model="form.sourceSites">
                <el-checkbox value="boss">BOSS直聘</el-checkbox>
                <el-checkbox value="zhaopin">智联招聘</el-checkbox>
                <el-checkbox value="51job">前程无忧</el-checkbox>
                <el-checkbox value="liepin">猎聘</el-checkbox>
                <el-checkbox value="lagou">拉勾网</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="关键词">
              <el-input v-model="form.keyword" placeholder="Java,Python,前端,测试,网络工程,大数据" />
              <div class="form-tip">多个关键词用逗号或英文逗号分隔，留空使用默认关键词</div>
            </el-form-item>
            <el-form-item label="城市">
              <el-select v-model="form.city" placeholder="选择目标城市" clearable filterable style="width: 100%">
                <el-option label="北京" value="北京" />
                <el-option label="上海" value="上海" />
                <el-option label="广州" value="广州" />
                <el-option label="深圳" value="深圳" />
                <el-option label="杭州" value="杭州" />
                <el-option label="长沙" value="长沙" />
                <el-option label="武汉" value="武汉" />
                <el-option label="成都" value="成都" />
                <el-option label="南京" value="南京" />
                <el-option label="西安" value="西安" />
                <el-option label="重庆" value="重庆" />
              </el-select>
              <div class="form-tip">留空则默认为长沙</div>
            </el-form-item>

            <el-form-item>
              <el-button type="primary" :loading="loading" @click="startCrawl" size="large">
                <el-icon><Connection /></el-icon>
                启动爬虫任务
              </el-button>
              <el-button size="large" @click="resetForm">
                <el-icon><RefreshLeft /></el-icon>
                重置
              </el-button>
            </el-form-item>
          </el-form>

          <div v-if="taskId" class="task-result">
            <el-alert :title="'任务创建成功！任务ID: ' + taskId" type="success" show-icon :closable="false" />
          </div>
        </el-card>
      </el-col>

      <el-col :span="8">
        <!-- 运行状态卡片 -->
        <el-card class="status-card" shadow="never">
          <template #header>
            <div class="card-header">
              <div class="title">
                <el-icon><Monitor /></el-icon>
                <span>运行状态</span>
              </div>
            </div>
          </template>

          <div class="status-stats">
            <div class="status-stat-item">
              <div class="status-value">{{ tasks.length }}</div>
              <div class="status-label">总任务数</div>
            </div>
            <div class="status-divider"></div>
            <div class="status-stat-item">
              <div class="status-value running">{{ runningCount }}</div>
              <div class="status-label">运行中</div>
            </div>
            <div class="status-divider"></div>
            <div class="status-stat-item">
              <div class="status-value finished">{{ finishedCount }}</div>
              <div class="status-label">已完成</div>
            </div>
          </div>
        </el-card>

        <!-- 最近任务列表 -->
        <el-card class="task-list-card" shadow="never" style="margin-top: 20px">
          <template #header>
            <div class="card-header">
              <div class="title">
                <el-icon><List /></el-icon>
                <span>最近任务</span>
              </div>
            </div>
          </template>
          <div class="mini-task-list" v-if="tasks.length > 0">
            <div v-for="task in recentTasks" :key="task.id" class="mini-task-item">
              <div class="mini-task-info">
                <el-tag :type="getStatusType(task.status)" size="small" effect="dark" round>
                  {{ getStatusText(task.status) }}
                </el-tag>
                <span class="mini-task-site">{{ task.sourceSite || '多平台' }}</span>
              </div>
              <div class="mini-job-count">{{ task.jobCount }}条</div>
            </div>
          </div>
          <el-empty v-else description="暂无任务记录" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref, computed } from 'vue';
import { ElMessage } from 'element-plus';
import { Setting, Connection, RefreshLeft, Monitor, List } from '@element-plus/icons-vue';
import { createCrawlTask, fetchCrawlTasks, startCrawlTask } from '@/api/crawl';

const form = reactive({
  sourceSites: ['boss', 'zhaopin', '51job'],
  keyword: 'Java,Python,前端,测试,大数据',
  city: '长沙'
});

const loading = ref(false);
const taskId = ref<number | null>(null);
const tasks = ref<any[]>([]);
let timer: number | undefined;

const runningCount = computed(() => tasks.value.filter(t => t.status === 'RUNNING').length);
const finishedCount = computed(() => tasks.value.filter(t => t.status === 'FINISHED').length);
const recentTasks = computed(() => tasks.value.slice(0, 8));

const startCrawl = async () => {
  if (!form.sourceSites.length) {
    ElMessage.warning('请至少选择一个数据来源');
    return;
  }
  loading.value = true;
  try {
    const createRes = await createCrawlTask({
      sourceSite: form.sourceSites.join(','),
      keyword: form.keyword,
      city: form.city
    });
    const id = createRes as any as number;
    taskId.value = id;
    await startCrawlTask(id);
    ElMessage.success(`爬虫任务已启动 (ID: ${id})`);
    await loadTasks();
  } finally {
    loading.value = false;
  }
};

const resetForm = () => {
  form.sourceSites = ['boss', 'zhaopin', '51job'];
  form.keyword = 'Java,Python,前端,测试,大数据';
  form.city = '长沙';
  taskId.value = null;
};

const loadTasks = async () => {
  tasks.value = (await fetchCrawlTasks()) as any[];
};

const getStatusType = (status: string) => {
  const types: Record<string, string> = { PENDING: 'info', RUNNING: 'warning', FINISHED: 'success', FAILED: 'danger' };
  return types[status] || 'info';
};

const getStatusText = (status: string) => {
  const texts: Record<string, string> = { PENDING: '待执行', RUNNING: '执行中', FINISHED: '已完成', FAILED: '失败' };
  return texts[status] || status;
};

onMounted(async () => {
  await loadTasks();
  timer = window.setInterval(loadTasks, 5000);
});

onUnmounted(() => {
  if (timer) {
    clearInterval(timer);
  }
});
</script>

<style scoped>
.settings-page {
  padding: 20px;
}

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
  color: #303133;
}

.setting-card {
  border: none !important;
  border-radius: 16px !important;
  background: linear-gradient(135deg, #ffffff 0%, #f8f9fc 100%) !important;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06) !important;
}

.form-tip {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}

.task-result {
  margin-top: 20px;
}

.status-card {
  border: none !important;
  border-radius: 16px !important;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06) !important;
}

.task-list-card {
  border: none !important;
  border-radius: 16px !important;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06) !important;
}

.status-stats {
  display: flex;
  align-items: center;
  justify-content: space-around;
  padding: 16px 0;
}

.status-stat-item {
  text-align: center;
}

.status-value {
  font-size: 28px;
  font-weight: 800;
  color: #303133;
}

.status-value.running {
  color: #e6a23c;
}

.status-value.finished {
  color: #67c23a;
}

.status-label {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}

.status-divider {
  width: 1px;
  height: 40px;
  background: #ebeef5;
}

.mini-task-list {
  max-height: 300px;
  overflow-y: auto;
}

.mini-task-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid #f5f7fa;
}

.mini-task-item:last-child {
  border-bottom: none;
}

.mini-task-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mini-task-site {
  font-size: 13px;
  color: #606266;
}

.mini-job-count {
  font-size: 14px;
  font-weight: 600;
  color: #409eff;
}
</style>
