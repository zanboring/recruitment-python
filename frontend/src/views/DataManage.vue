<template>
  <div class="data-manage-page">
    <!-- 操作按钮栏 -->
    <el-row :gutter="20">
      <el-col :span="24">
        <DataManageHeader
          v-model:showCrawlDialog="showCrawlDialog"
          :cleaning-data="cleaningData"
          :quick-crawling="quickCrawling"
          @load-data="loadData"
          @start-quick-crawl="startQuickCrawl"
        />
      </el-col>
    </el-row>

    <!-- 数据统计卡片 -->
    <StatCards
      :total-jobs="totalJobs"
      :active-jobs="activeJobs"
      :offline-jobs="offlineJobs"
      :total-tasks="totalTasks"
    />

    <!-- 爬取进度条 -->
    <CrawlProgress
      v-if="quickCrawling"
      :crawl-progress="crawlProgress"
      :crawl-status="crawlStatus"
    />

    <!-- 实时爬取日志 -->
    <CrawlLogPanel
      ref="crawlLogPanelRef"
      :crawl-logs="crawlLogs"
      :quick-crawling="quickCrawling"
      @clear-logs="clearLogs"
    />

    <!-- 爬虫任务列表 -->
    <TaskTable
      :tasks="tasks"
      :loading="loading"
      @load-data="loadData"
      @view-task-log="viewTaskLog"
    />

    <!-- 数据预览 -->
    <DataPreviewTable
      :preview-data="previewData"
      :preview-loading="previewLoading"
      @load-preview-data="loadPreviewData"
    />

    <!-- 创建爬虫任务对话框 -->
    <CrawlTaskDialog
      v-model:visible="showCrawlDialog"
      :creating-task="creatingTask"
      @created="loadData"
    />

    <!-- 任务日志对话框 -->
    <TaskLogDialog
      v-model:visible="showLogDialog"
      :task="currentTask"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted } from 'vue';
import { ElMessage } from 'element-plus';
import http from '@/api/http';

// 子组件
import DataManageHeader from './components/DataManageHeader.vue';
import StatCards from './components/StatCards.vue';
import CrawlProgress from './components/CrawlProgress.vue';
import CrawlLogPanel from './components/CrawlLogPanel.vue';
import TaskTable from './components/TaskTable.vue';
import DataPreviewTable from './components/DataPreviewTable.vue';
import CrawlTaskDialog from './components/CrawlTaskDialog.vue';
import TaskLogDialog from './components/TaskLogDialog.vue';

// 类型定义
interface LogItem {
  type: 'info' | 'success' | 'warning' | 'error';
  time: string;
  message: string;
}

interface Task {
  id: number;
  sourceSite: string;
  keyword: string;
  city: string;
  status: string;
  jobCount: number;
  createdAt: string;
  finishedAt: string;
  message?: string;
}

interface Job {
  id?: number;
  title: string;
  companyName: string;
  city: string;
  experience: string;
  education: string;
  minSalary?: number;
  maxSalary?: number;
  sourceSite: string;
  createdAt: string;
  url?: string;
}

interface PageResult<T> {
  list: T[];
  total: number;
}

// 配置常量
const CRAWL_CONFIG = {
  defaultCities: ['长沙'] as const,
  keywords: [
    'Java后端', '运维', '软件测试', 'Python开发', '计算机相关',
    '应届生', '校招', '25届', '26届', '无经验', '0-1年', '1-3年', '实习',
  ] as const,
  platforms: [
    { name: 'BOSS', code: 'boss' },
    { name: '智联招聘', code: 'zhaopin' },
    { name: '前程无忧', code: '51job' },
    { name: '猎聘', code: 'liepin' },
    { name: '拉勾网', code: 'lagou' },
    { name: '牛客网', code: 'nowcoder' },
  ] as const,
  maxPollRounds: 90,
  pollIntervalMs: 8000,
  delayBetweenSites: 3000,
  maxLogCount: 200,
} as const;

// 状态
const loading = ref(false);
const previewLoading = ref(false);
const creatingTask = ref(false);
const cleaningData = ref(false);
const quickCrawling = ref(false);
const tasks = ref<Task[]>([]);
const previewData = ref<Job[]>([]);
const showCrawlDialog = ref(false);
const showLogDialog = ref(false);
const currentTask = ref<Task | null>(null);
const crawlLogs = ref<LogItem[]>([]);
const crawlLogPanelRef = ref<InstanceType<typeof CrawlLogPanel> | null>(null);
const quickTaskIds = ref<number[]>([]);
const quickPollTimer = ref<number | null>(null);
const totalJobs = ref(0);
const activeJobs = ref(0);
const offlineJobs = ref(0);
const totalTasks = ref(0);
const crawlProgress = ref(0);
const crawlStatus = ref('准备中');

type LogType = 'info' | 'success' | 'warning' | 'error';

const unwrapResponse = <T>(res: unknown): T => {
  if (res && typeof res === 'object' && 'data' in (res as Record<string, unknown>)) {
    return (res as { data: T }).data;
  }
  return res as T;
};

const addLog = (message: string, type: LogType = 'info') => {
  const now = new Date();
  const time = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`;

  crawlLogs.value.push({
    type: type as LogItem['type'],
    time,
    message
  });

  if (crawlLogs.value.length > CRAWL_CONFIG.maxLogCount) {
    crawlLogs.value.splice(0, crawlLogs.value.length - CRAWL_CONFIG.maxLogCount);
  }

  nextTick(() => {
    const el = crawlLogPanelRef.value?.logContentRef;
    if (el) {
      el.scrollTo({
        top: el.scrollHeight,
        behavior: 'smooth'
      });
    }
  });
};

const clearLogs = () => {
  crawlLogs.value = [];
  quickTaskIds.value = [];
};

const loadData = async () => {
  loading.value = true;
  try {
    const res = await http.get('/crawl/tasks');
    const data = unwrapResponse<Task[] | { list: Task[] }>(res);
    tasks.value = Array.isArray(data) ? data : (data.list || []);
    totalTasks.value = tasks.value.length;
    await loadJobStats();
  } catch (error: unknown) {
    ElMessage.error('加载任务列表失败');
  } finally {
    loading.value = false;
  }
};

const loadJobStats = async () => {
  try {
    const [totalRes, activeRes, offlineRes] = await Promise.all([
      http.post('/jobs/page', { pageNum: 1, pageSize: 1 }),
      http.post('/jobs/page', { pageNum: 1, pageSize: 1, status: 'ACTIVE' }),
      http.post('/jobs/page', { pageNum: 1, pageSize: 1, status: 'OFFLINE' })
    ]);
    
    totalJobs.value = unwrapResponse<PageResult<Job>>(totalRes).total ?? 0;
    activeJobs.value = unwrapResponse<PageResult<Job>>(activeRes).total ?? 0;
    offlineJobs.value = unwrapResponse<PageResult<Job>>(offlineRes).total ?? 0;
  } catch (error: unknown) {
    ElMessage.warning('加载统计数据失败');
  }
};

const loadPreviewData = async (query?: { keyword: string }) => {
  previewLoading.value = true;
  try {
    const res = await http.post('/jobs/page', {
      keyword: query?.keyword ?? '',
      pageNum: 1,
      pageSize: 10
    });
    const pageResult = unwrapResponse<PageResult<Job>>(res);
    previewData.value = pageResult.list ?? [];
  } catch (error: unknown) {
    ElMessage.error('加载数据预览失败');
  } finally {
    previewLoading.value = false;
  }
};

const viewTaskLog = (task: Task) => {
  currentTask.value = task;
  showLogDialog.value = true;
};

const stopQuickPolling = () => {
  if (quickPollTimer.value != null) {
    window.clearInterval(quickPollTimer.value);
    quickPollTimer.value = null;
  }
};

const updateQuickTasksStatus = async (): Promise<Task[]> => {
  try {
    const res = await http.get('/crawl/tasks');
    const data = unwrapResponse<Task[] | { list: Task[] }>(res);
    const allTasks = Array.isArray(data) ? data : (data.list || []);
    const quickTasks = allTasks.filter((t) => quickTaskIds.value.includes(t.id));
    
    tasks.value = tasks.value.map(task => {
      const updatedTask = quickTasks.find((t) => t.id === task.id);
      return updatedTask && updatedTask.status !== task.status ? updatedTask : task;
    });
    
    return quickTasks;
  } catch (error: unknown) {
    ElMessage.warning('轮询任务状态失败，稍后重试');
    return [];
  }
};

const startQuickPolling = () => {
  stopQuickPolling();
  let rounds = 0;
  const total = quickTaskIds.value.length;
  quickPollTimer.value = window.setInterval(async () => {
    rounds++;
    try {
      const quickTasks = await updateQuickTasksStatus();
      const running = quickTasks.filter((t) => t.status === 'RUNNING').length;
      const finished = quickTasks.filter((t) => t.status === 'FINISHED' || t.status === 'FAILED').length;
      addLog(`轮询状态：运行中 ${running} 个，已结束 ${finished}/${total}`);

      const safeTotal = total || 1;
      const progress = 40 + Math.round((finished / safeTotal) * 60);
      crawlProgress.value = Math.min(progress, 100);
      
      if (running > 0) {
        crawlStatus.value = `正在爬取中，已完成 ${finished}/${total} 个任务`;
      } else if (finished < total) {
        crawlStatus.value = `等待任务执行，已完成 ${finished}/${total} 个任务`;
      } else {
        crawlStatus.value = '所有任务已完成';
      }

      if (finished === total || rounds >= CRAWL_CONFIG.maxPollRounds) {
        stopQuickPolling();
        await loadPreviewData();
        await loadJobStats();
        quickCrawling.value = false;
        crawlProgress.value = 100;
        crawlStatus.value = '爬取完成';
        addLog('✓ 爬取任务轮询结束，数据已刷新', 'success');
        addLog('========================================');
        addLog('爬取流程结束');
        addLog('========================================');
      }
    } catch (error: unknown) {
      addLog('轮询任务状态异常，稍后将自动重试', 'warning');
    }
  }, CRAWL_CONFIG.pollIntervalMs);
};

const startQuickCrawl = async () => {
  stopQuickPolling();
  quickCrawling.value = true;
  crawlLogs.value = [];
  quickTaskIds.value = [];
  crawlProgress.value = 0;
  crawlStatus.value = '准备中';
  
  const defaultCities = CRAWL_CONFIG.defaultCities;
  const keywords = [...CRAWL_CONFIG.keywords];
  const sites = CRAWL_CONFIG.platforms;
  
  addLog('========================================');
  addLog('开始一键实时爬取所有平台');
  addLog(`目标城市：${defaultCities.join('、')}`);
  addLog(`目标平台：${sites.map(s => s.name).join('、')}`);
  addLog(`关键词：${keywords.join(', ')}`);
  addLog('========================================');

  try {
    const city = defaultCities.join(',');
    let totalCreated = 0;
    let totalFailed = 0;

    for (let i = 0; i < sites.length; i++) {
      const site = sites[i];
      addLog(`----------------------------------------`);
      addLog(`正在爬取 ${site.name}...`);
      crawlStatus.value = `正在爬取 ${site.name}`;
      crawlProgress.value = Math.round((i / sites.length) * 30);

      const task = {
        sourceSite: site.code,
        keyword: keywords.join(','),
        city: city
      };

      try {
        addLog(`创建 ${site.name} 爬取任务...`);
        const taskRes = await http.post('/crawl/task', task);
        const result = unwrapResponse<{ id: number } | number>(taskRes);
                                                                                          const taskId = typeof result === 'number' ? result : (result.id ?? 0);

        if (taskId) {
          totalCreated++;
          quickTaskIds.value.push(taskId);
          addLog(`✓ ${site.name} 任务创建成功 (任务ID: ${taskId})`, 'success');
          
          addLog(`启动 ${site.name} 爬取任务...`);
          await http.post(`/crawl/task/${taskId}/start`);
          addLog(`✓ ${site.name} 任务已启动`, 'success');
        } else {
          totalFailed++;
          addLog(`✗ ${site.name} 任务创建失败：无效的响应`, 'error');
        }
      } catch (error: unknown) {
        totalFailed++;
        const errorMsg = error instanceof Error ? error.message : '未知错误';
        addLog(`✗ ${site.name} 任务创建失败：${errorMsg}`, 'error');
      }

      await new Promise(resolve => setTimeout(resolve, CRAWL_CONFIG.delayBetweenSites));
    }

    crawlProgress.value = 40;
    crawlStatus.value = '爬取任务已启动，等待执行完成';
    
    addLog('========================================');
    addLog(`所有平台爬取任务已创建完成`, 'success');
    addLog(`成功创建 ${totalCreated} 个爬取任务`, 'success');
    if (totalFailed > 0) {
      addLog(`失败 ${totalFailed} 个爬取任务`, 'warning');
    }
    addLog('正在后台执行爬取，系统将自动轮询任务状态...');
    addLog('========================================');
    
    ElMessage.success('一键爬取任务已启动，正在后台执行');

    startQuickPolling();

  } catch (error: unknown) {
    const errorMsg = error instanceof Error ? error.message : '未知错误';
    addLog(`爬取失败：${errorMsg}`, 'error');
    addLog('请检查网络连接或后端服务是否正常', 'error');
    ElMessage.error('爬取失败，请检查网络连接或后端服务');
    quickCrawling.value = false;
    crawlProgress.value = 0;
    crawlStatus.value = '爬取失败';
  }
};

onMounted(() => {
  loadData();
  loadPreviewData();
});

onUnmounted(() => {
  stopQuickPolling();
});
</script>

<style scoped>
.data-manage-page {
  padding: 20px;
}
</style>
