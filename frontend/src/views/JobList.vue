<template>
  <div class="job-list-container">
    <!-- 筛选栏子组件 -->
    <FilterSection
      v-model:query="query"
      @search="loadData" @reset="resetFilters"
      @ai-analysis="handleAIAnalysis" @salary-predict="handleSalaryPredict"
      @recommend="handleRecommend" @quick-crawl="startQuickCrawl" @export-excel="exportToExcel"
    />

    <!-- 岗位卡片网格 + 分页 -->
    <div class="job-cards">
      <el-row :gutter="16" class="result-info-row">
        <el-col :span="12">
          <div class="result-info">
            <el-icon><InfoFilled /></el-icon>
            共找到 <span class="highlight">{{ total }}</span> 个符合条件岗位
          </div>
        </el-col>
        <el-col :span="12" class="result-actions">
          <el-radio-group v-model="query.pageSize" @change="loadData" size="small">
            <el-radio-button :value="12">12</el-radio-button>
            <el-radio-button :value="24">24</el-radio-button>
            <el-radio-button :value="36">36</el-radio-button>
          </el-radio-group>
        </el-col>
      </el-row>

      <el-row :gutter="20" v-loading="loading" class="job-grid">
        <el-col :span="8" v-for="job in list" :key="job.id" class="job-col">
          <JobCard :job="job" @click="handleJobClick(job)" />
        </el-col>
      </el-row>

      <el-row :gutter="16" v-if="list.length === 0 && !loading">
        <el-col :span="24">
          <el-empty description="暂无岗位数据，请先执行爬虫任务获取数据">
            <el-button type="primary" @click="$router.push('/data')">前往数据管理</el-button>
          </el-empty>
        </el-col>
      </el-row>

      <div class="pagination-container" v-if="total > 0">
        <el-pagination
          v-model:current-page="query.pageNum"
          v-model:page-size="query.pageSize"
          :page-sizes="[12, 24, 36]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          size="default"
          @current-change="loadData"
          @size-change="loadData"
        />
      </div>
    </div>

    <!-- AI分析弹窗 -->
    <AIAnalysisDialog v-model:visible="showAIAnalysis" :loading="aiLoading" :result="aiResult" />

    <!-- 薪资预测弹窗 -->
    <SalaryPredictDialog
      v-model:visible="showSalaryPredict"
      :loading="salaryLoading"
      :predicted-salary="predictedSalary"
      :salary-factors="salaryFactors"
      :total-jobs="total"
      :init-city="query.city"
      :init-education="query.education"
      :init-experience="query.experience"
      @predict="doSalaryPredict"
    />

    <!-- 智能推荐弹窗 -->
    <RecommendDialog
      v-model:visible="showRecommend"
      :loading="recommendLoading"
      :recommend-list="recommendList"
      :init-city="query.city"
      @search="doRecommend"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue';
import { InfoFilled } from '@element-plus/icons-vue';
import { fetchJobPage, aiAnalysis, predictSalary, recommendJobs } from '@/api/job';
import http from '@/api/http';
import FilterSection from './components/FilterSection.vue';
import JobCard from './components/JobCard.vue';
import AIAnalysisDialog from './components/AIAnalysisDialog.vue';
import SalaryPredictDialog from './components/SalaryPredictDialog.vue';
import RecommendDialog from './components/RecommendDialog.vue';
import { ElMessage } from 'element-plus';
import { isPreciseJobSourceUrl } from '@/utils/jobSourceUrl';

const loading = ref(false);
const list = ref<any[]>([]);
const total = ref(0);
const showAIAnalysis = ref(false);
const aiLoading = ref(false);
const quickCrawling = ref(false);

// 薪资预测相关
const showSalaryPredict = ref(false);
const salaryLoading = ref(false);
const predictedSalary = ref<number | null>(null);
const salaryFactors = ref<Array<{ label: string; type: string }>>([]);

// 智能推荐相关
const showRecommend = ref(false);
const recommendLoading = ref(false);
const recommendList = ref<any[]>([]);

// AI分析结果
const aiResult = ref<any>({
  summary: '',
  qualityJobs: [],
  trendAnalysis: {},
  skillDemands: [],
  salaryAnalysis: {},
  suggestions: []
});

const query = reactive({
  keyword: '',
  city: '',
  sourceSite: '',
  education: '',
  experience: '',
  minSalary: undefined as number | undefined,
  maxSalary: undefined as number | undefined,
  status: '',
  pageNum: 1,
  pageSize: 12
});

const loadData = async () => {
  loading.value = true;
  try {
    const res = await fetchJobPage(query);
    list.value = res.list || [];
    total.value = res.total || 0;
  } catch (error) {
    ElMessage.error('加载数据失败');
  } finally {
    loading.value = false;
  }
};

const resetFilters = () => {
  query.keyword = '';
  query.city = '';
  query.sourceSite = '';
  query.education = '';
  query.experience = '';
  query.minSalary = undefined;
  query.maxSalary = undefined;
  query.pageNum = 1;
  loadData();
};

/** 一键爬取
 * 【优化】仅启用 BOSS直聘 + 智联招聘（前程无忧/猎聘已从后端SITE_MAP禁用）
 * 代码保留，可按需在 CrawlServiceImpl.SITE_MAP 中重新启用
 */
const startQuickCrawl = async () => {
  quickCrawling.value = true;
  ElMessage.info('开始一键爬取长沙岗位数据...');

  try {
    const sites = [
      { name: 'BOSS直聘', code: 'boss' },
      { name: '智联招聘', code: 'zhaopin' }
      // { name: '前程无忧', code: '51job' },  // 【已禁用】备用数据充足，无需额外来源（代码保留）
      // { name: '猎聘', code: 'liepin' }       // 【已禁用】备用数据充足，无需额外来源（代码保留）
    ];
    const keywords = ['Java', '前端', 'Python', '大数据', '运维', '测试'];
    const city = '长沙';
    let successCount = 0;

    for (const site of sites) {
      try {
        const task = { sourceSite: site.code, keyword: keywords.join(','), city };
        const taskRes = await http.post('/crawl/task', task);
        const taskId = typeof taskRes === 'number' ? taskRes : (taskRes as any).id || taskRes;
        await http.post(`/crawl/task/${taskId}/start`);
        successCount++;
      } catch (error) {
        console.error(`${site.name} 爬取失败:`, error);
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }

    ElMessage.success(`成功创建 ${successCount} 个爬取任务，正在后台执行...`);
    setTimeout(() => { loadData(); ElMessage.success('数据已刷新'); }, 8000);
  } catch (error) {
    ElMessage.error('爬取失败，请检查网络连接');
  } finally {
    setTimeout(() => { quickCrawling.value = false; }, 10000);
  }
};

/** 导出Excel */
const exportToExcel = async () => {
  try {
    ElMessage.info('正在导出Excel...');
    const response = await http.get('/data/export', {
      responseType: 'blob',
      params: {
        keyword: query.keyword, city: query.city, sourceSite: query.sourceSite,
        education: query.education, experience: query.experience,
        minSalary: query.minSalary, maxSalary: query.maxSalary, status: query.status
      }
    });
    // 拦截器对 blob 请求直接返回 Blob 对象，无需再取 .data
    const blob = response instanceof Blob ? response : (response as any).data;
    const blobData = blob instanceof Blob ? blob : new Blob([blob], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const url = window.URL.createObjectURL(blobData);
    const link = document.createElement('a');
    link.href = url;
    const now = new Date();
    const dateStr = `${now.getFullYear()}${(now.getMonth()+1).toString().padStart(2,'0')}${now.getDate().toString().padStart(2,'0')}_${now.getHours().toString().padStart(2,'0')}${now.getMinutes().toString().padStart(2,'0')}${now.getSeconds().toString().padStart(2,'0')}`;
    link.download = `招聘数据_${dateStr}.xlsx`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
    ElMessage.success('Excel导出成功');
  } catch (error) {
    ElMessage.error('导出失败，请重试');
  }
};

/** AI分析 */
const handleAIAnalysis = async () => {
  aiLoading.value = true;
  try {
    const filterQuery = {
      keyword: query.keyword, city: query.city, sourceSite: query.sourceSite,
      education: query.education, experience: query.experience,
      minSalary: query.minSalary, maxSalary: query.maxSalary, status: query.status
    };
    const res = await aiAnalysis(filterQuery);
    aiResult.value = res || aiResult.value;
    showAIAnalysis.value = true;
  } catch (error) {
    ElMessage.error('AI分析失败');
  } finally {
    aiLoading.value = false;
  }
};

/** 打开薪资预测弹窗 */
const handleSalaryPredict = () => {
  predictedSalary.value = null; salaryFactors.value = [];
  showSalaryPredict.value = true;
};

/** 执行薪资预测 */
const doSalaryPredict = async (form: any) => {
  salaryLoading.value = true;
  try {
    const result = await predictSalary({
      city: form.city || undefined, experience: form.experience || undefined,
      education: form.education || undefined, skills: form.skills || undefined
    });
    predictedSalary.value = Number(result);
    const factors: Array<{ label: string; type: string }> = [];
    if (form.city) factors.push({ label: `城市: ${form.city}`, type: 'primary' });
    if (form.experience) factors.push({ label: `经验: ${form.experience}`, type: 'success' });
    if (form.education) factors.push({ label: `学历: ${form.education}`, type: 'warning' });
    if (form.skills) factors.push({ label: `技能: ${form.skills}`, type: 'danger' });
    salaryFactors.value = factors;
    ElMessage.success('预测完成');
  } catch (error) {
    ElMessage.error('预测失败，请重试');
  } finally {
    salaryLoading.value = false;
  }
};

/** 打开智能推荐 */
const handleRecommend = () => {
  recommendList.value = [];
  showRecommend.value = true;
};

/** 执行岗位推荐匹配 */
const doRecommend = async ({ skills, education, experience, city }: { skills?: string; education?: string; experience?: number; city?: string }) => {
  recommendLoading.value = true;
  try {
    const result = await recommendJobs({ 
      skills: skills || undefined, 
      education: education || undefined,
      experience: experience,
      city: city || undefined 
    });
    recommendList.value = result || [];
    ElMessage.success(`匹配到 ${recommendList.value.length} 个推荐岗位`);
  } catch (error) {
    ElMessage.error('推荐失败，请重试');
  } finally {
    recommendLoading.value = false;
  }
};

onMounted(() => { loadData(); });

/** 点击岗位卡片：优先打开真实详情页，无详情页时打开搜索列表页作为兜底 */
const handleJobClick = (job: any) => {
  const url = job.url;
  
  if (!url || typeof url !== 'string' || url.trim() === '') {
    ElMessage.warning('该岗位暂无外部链接');
    return;
  }
  
  const trimmedUrl = url.trim();
  
  if (isPreciseJobSourceUrl(trimmedUrl)) {
    window.open(trimmedUrl, '_blank', 'noopener,noreferrer');
  } else if (trimmedUrl.includes('zhipin.com') || trimmedUrl.includes('zhaopin.com') || 
             trimmedUrl.includes('51job.com') || trimmedUrl.includes('liepin.com')) {
    window.open(trimmedUrl, '_blank', 'noopener,noreferrer');
  } else {
    ElMessage.warning('该岗位的外部链接无效');
  }
};
</script>

<style scoped>
.job-list-container {
  padding: 16px;
}

.result-info-row {
  margin-bottom: 16px; align-items: center;
}
.result-info {
  display: flex; align-items: center; gap: 8px;
  font-size: 14px; color: var(--text-regular);
}
.result-info .el-icon { color: var(--info-color); font-size: 16px; }
.result-info .highlight {
  color: var(--info-color); font-weight: 700; font-size: 18px;
}
.result-actions { display: flex; justify-content: flex-end; }

.job-grid { min-height: 300px; }
.job-col { margin-bottom: 20px; }

.pagination-container {
  display: flex; justify-content: center; margin-top: 30px; padding: 20px 0;
}

/* ========== 全局组件增强样式（保留原有视觉效果）========== */
.job-list-container :deep(.el-row) { margin-left: 0 !important; margin-right: 0 !important; }
.job-list-container :deep(.el-col) { padding-left: 10px !important; padding-right: 10px !important; }

/* 卡片悬浮效果 */
.job-list-container :deep(.el-card:hover) { transform: translateY(-4px); box-shadow: 0 12px 40px rgba(0,0,0,.12) !important; }

/* 输入框聚焦 */
.job-list-container :deep(.el-input__wrapper:focus-within) {
  box-shadow: 0 0 0 3px rgba(102,126,234,.2) !important;
  border-color: var(--primary-color) !important;
}

/* 选择器聚焦 */
.job-list-container :deep(.el-select .el-input.is-focus .el-input__wrapper) {
  box-shadow: 0 0 0 3px rgba(102,126,234,.2) !important;
}

/* 主按钮渐变 */
.job-list-container :deep(.el-button--primary) {
  background: linear-gradient(135deg, var(--primary-color), var(--primary-light)) !important;
  border: none !important; border-radius: 10px !important; font-weight: 500 !important;
}
.job-list-container :deep(.el-button--primary:hover) {
  transform: translateY(-2px); box-shadow: 0 6px 20px rgba(102,126,234,.4) !important;
}

/* 成功按钮 */
.job-list-container :deep(.el-button--success) {
  background: linear-gradient(135deg, var(--success-color), #38f9d7) !important;
  border: none !important; border-radius: 10px !important;
}
.job-list-container :deep(.el-button--success:hover) {
  transform: translateY(-2px); box-shadow: 0 6px 20px rgba(67,233,123,.4) !important;
}

/* 表格增强 */
.job-list-container :deep(.el-table) { border-radius: 16px !important; overflow: hidden !important; border: 1px solid var(--border-extra-light) !important; }
.job-list-container :deep(.el-table th.el-table__cell) {
  background: linear-gradient(135deg, #f8f9fc, #f4f6f9) !important;
  color: var(--text-regular) !important; font-weight: 600 !important;
}
.job-list-container :deep(.el-table td.el-table__cell) { color: var(--text-regular) !important; }
.job-list-container :deep(.el-table--enable-row-hover .el-table__body tr:hover>td) {
  background: linear-gradient(135deg, rgba(102,126,234,.05), rgba(118,75,162,.05)) !important;
}

/* 分页增强 */
.job-list-container :deep(.el-pagination .el-pager li.is-active) {
  background: linear-gradient(135deg, var(--primary-color), var(--primary-light)) !important; color: #fff !important;
}
.job-list-container :deep(.el-pagination .el-pager li:hover) { color: var(--primary-color) !important; }
.job-list-container :deep(.el-pagination .btn-prev),
.job-list-container :deep(.el-pagination .btn-next) { border-radius: 8px !important; }
.job-list-container :deep(.el-pagination__total) { color: var(--text-secondary) !important; }

/* 标签增强 */
.job-list-container :deep(.el-tag) { border-radius: 20px !important; border: none !important; padding: 0 12px !important; font-weight: 500 !important; font-size: 12px !important; }

/* 对话框增强 */
.job-list-container :deep(.el-dialog) { border-radius: 20px !important; overflow: hidden !important; }
.job-list-container :deep(.el-dialog__header) {
  background: linear-gradient(135deg, var(--primary-color), var(--primary-light)) !important;
  padding: 20px 24px !important; margin: 0; border-radius: 20px 20px 0 0 !important;
}
.job-list-container :deep(.el-dialog__title) { color: #fff !important; font-weight: 600 !important; }
.job-list-container :deep(.el-dialog__headerbtn .el-dialog__close) { color: #fff !important; }
.job-list-container :deep(.el-dialog__body) { padding: 24px !important; }

/* 加载状态 */
.job-list-container :deep(.el-loading-mask) { background: rgba(255,255,255,.9) !important; border-radius: 16px !important; }
.job-list-container :deep(.el-loading-spinner .circular) { stroke: var(--primary-color) !important; }

/* 消息提示 */
.job-list-container :deep(.el-message) { border-radius: 8px !important; border: none !important; box-shadow: 0 8px 30px rgba(0,0,0,.15) !important; }

/* 空状态 */
.job-list-container :deep(.el-empty__description) { color: var(--text-secondary) !important; }
</style>
