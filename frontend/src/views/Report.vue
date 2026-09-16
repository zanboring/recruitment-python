<template>
  <div class="report-page">
    <!-- 顶部操作栏 -->
    <div class="page-toolbar">
      <div class="toolbar-left">
        <h2 class="page-title">自动化日报</h2>
        <el-tag v-if="latest" :type="latest.generated_by === 'rule' ? 'info' : 'success'" effect="light" size="small">
          {{ genLabel(latest.generated_by) }}
        </el-tag>
      </div>
      <el-button type="primary" :loading="generating" @click="handleGenerate">
        <el-icon><RefreshRight /></el-icon>&nbsp;生成当日日报
      </el-button>
    </div>

    <!-- 最新日报概览 -->
    <el-card v-if="latest" shadow="hover" class="latest-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">
            <el-icon><Document /></el-icon>&nbsp;{{ latest.title }}
          </span>
          <el-button v-if="latest.has_excel" link type="primary" @click="handleDownload(latest.id)">
            <el-icon><Download /></el-icon>&nbsp;下载 Excel
          </el-button>
        </div>
      </template>

      <!-- 指标卡片 -->
      <el-row :gutter="16" class="stat-row">
        <el-col :span="6"><StatCard label="今日新增岗位" :value="latest.new_jobs" color="#409eff" icon="TrendCharts" /></el-col>
        <el-col :span="6"><StatCard label="在架岗位" :value="latest.active_jobs" color="#67c23a" icon="Briefcase" /></el-col>
        <el-col :span="6"><StatCard label="累计岗位" :value="latest.total_jobs" color="#e6a23c" icon="Files" /></el-col>
        <el-col :span="6"><StatCard label="平均薪资" :value="stats?.summary?.avg_salary ?? 0" suffix="元/月" color="#f56c6c" icon="Money" /></el-col>
      </el-row>

      <!-- AI 摘要 -->
      <div v-if="latest.ai_summary" class="ai-summary">
        <div class="summary-label"><el-icon><Cpu /></el-icon>&nbsp;AI 市场摘要</div>
        <p class="summary-text">{{ latest.ai_summary }}</p>
      </div>

      <!-- 统计图表（两列） -->
      <el-row :gutter="16" v-if="stats">
        <el-col :span="12">
          <ChartBlock title="城市岗位分布" :data="stats.city" color="#409eff" />
        </el-col>
        <el-col :span="12">
          <ChartBlock title="薪资区间分布" :data="stats.salary_range" color="#67c23a" />
        </el-col>
      </el-row>
    </el-card>

    <!-- 无日报占位 -->
    <el-empty v-else description="暂无日报，点击右上角「生成当日日报」立即生成" />

    <!-- 历史日报列表 -->
    <el-card v-if="reports.length" shadow="never" class="history-card">
      <template #header><span class="card-title">历史日报</span></template>
      <el-table :data="reports" stripe>
        <el-table-column prop="report_date" label="日期" width="130" />
        <el-table-column prop="title" label="标题" />
        <el-table-column label="生成方式" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.generated_by === 'rule' ? 'info' : 'success'">{{ genLabel(row.generated_by) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="new_jobs" label="今日新增" width="90" align="center" />
        <el-table-column prop="active_jobs" label="在架" width="90" align="center" />
        <el-table-column label="操作" width="120" align="center">
          <template #default="{ row }">
            <el-button v-if="row.has_excel" link type="primary" @click="handleDownload(row.id)">
              <el-icon><Download /></el-icon>&nbsp;Excel
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, h, type Component } from 'vue';
import { ElMessage } from 'element-plus';
import { RefreshRight, Document, Download, Cpu, TrendCharts, Briefcase, Files, Money } from '@element-plus/icons-vue';
import {
  generateReportApi, getLatestReportApi, getReportListApi, downloadReportExcelApi,
  type ReportDetail, type ReportSummary
} from '@/api/report';

const latest = ref<ReportDetail | null>(null);
const reports = ref<ReportSummary[]>([]);
const generating = ref(false);
const stats = computed(() => latest.value?.stats);

const genLabel = (v: string) => ({ primary: 'AI 云端生成', local: 'AI 本地生成', rule: '规则摘要' })[v] ?? v;

const load = async () => {
  try {
    const [l, list] = await Promise.all([getLatestReportApi(), getReportListApi(10)]);
    latest.value = l;
    reports.value = list;
  } catch {
    // 无日报时 latest 返回 404，此处静默（空态由模板兜底）
  }
};

const handleGenerate = async () => {
  generating.value = true;
  try {
    const r = await generateReportApi();
    ElMessage.success(r.status === 'GENERATED' ? '今日日报已生成' : '今日日报已存在');
    await load();
  } catch (e: any) {
    ElMessage.error(e?.message || '生成失败');
  } finally {
    generating.value = false;
  }
};

const handleDownload = async (id: number) => {
  const blob = await downloadReportExcelApi(id);
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `daily_report_${id}.xlsx`;
  a.click();
  URL.revokeObjectURL(url);
};

onMounted(load);

/* ---- 函数式小组件（h() 渲染，不依赖 JSX） ---- */
const ICONS: Record<string, Component> = { TrendCharts, Briefcase, Files, Money };

const StatCard = (props: Record<string, any>) => {
  const Icon: Component = ICONS[props.icon] || Briefcase;
  return h('div', { class: 'stat-card' }, [
    h('div', {
      class: 'stat-card-icon',
      style: { background: (props.color || '#409eff') + '1a', color: props.color }
    }, [h(Icon, { style: 'font-size:22px' })]),
    h('div', { class: 'stat-card-body' }, [
      h('div', { class: 'stat-card-value' }, [
        String((props.value ?? 0).toLocaleString()),
        props.suffix ? h('span', { class: 'stat-card-suffix' }, props.suffix) : null
      ]),
      h('div', { class: 'stat-card-label' }, props.label || '')
    ])
  ]);
};

const ChartBlock = (props: Record<string, any>) => {
  const rows: { name: string; count: number }[] = (props.data || []).slice(0, 8);
  const max = Math.max(1, ...rows.map((r) => r.count));
  return h('div', { class: 'chart-block' }, [
    h('div', { class: 'chart-block-title' }, props.title || ''),
    rows.length === 0
      ? h('div', { class: 'chart-block-empty' }, '暂无数据')
      : rows.map((r) =>
          h('div', { class: 'chart-bar-row' }, [
            h('span', { class: 'chart-bar-name' }, r.name),
            h('div', { class: 'chart-bar-track' }, [
              h('div', {
                class: 'chart-bar-fill',
                style: { width: (r.count / max) * 100 + '%', background: props.color || '#409eff' }
              })
            ]),
            h('span', { class: 'chart-bar-count' }, String(r.count))
          ])
        )
  ]);
};
</script>

<style scoped>
.page-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.toolbar-left { display: flex; align-items: center; gap: 12px; }
.page-title { margin: 0; font-size: 18px; font-weight: 600; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.card-title { display: flex; align-items: center; font-weight: 600; }
.stat-row { margin-bottom: 16px; }
.stat-card { display: flex; align-items: center; gap: 12px; padding: 16px; border: 1px solid #f0f0f0; border-radius: 10px; }
.stat-card-icon { width: 46px; height: 46px; border-radius: 10px; display: flex; align-items: center; justify-content: center; }
.stat-card-value { font-size: 20px; font-weight: 700; color: #303133; }
.stat-card-suffix { font-size: 12px; font-weight: 400; color: #909399; margin-left: 4px; }
.stat-card-label { font-size: 12px; color: #909399; margin-top: 2px; }
.ai-summary { padding: 14px 16px; background: linear-gradient(135deg, #f5f7ff, #f0f2fa); border-radius: 10px; margin-bottom: 16px; border-left: 3px solid #667eea; }
.summary-label { display: flex; align-items: center; gap: 4px; font-weight: 600; color: #667eea; font-size: 13px; margin-bottom: 8px; }
.summary-text { margin: 0; color: #4a4a68; line-height: 1.7; font-size: 13.5px; }
.chart-block { border: 1px solid #f0f0f0; border-radius: 10px; padding: 16px; }
.chart-block-title { font-weight: 600; margin-bottom: 12px; color: #303133; }
.chart-block-empty { color: #c0c4cc; text-align: center; padding: 16px 0; font-size: 13px; }
.chart-bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.chart-bar-name { width: 72px; text-align: right; font-size: 12px; color: #606266; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chart-bar-track { flex: 1; background: #f5f7fa; border-radius: 4px; height: 14px; overflow: hidden; }
.chart-bar-fill { height: 100%; border-radius: 4px; transition: width .4s ease; }
.chart-bar-count { width: 44px; font-size: 12px; color: #909399; }
.latest-card { margin-bottom: 16px; }
.history-card :deep(.el-card__header) { padding: 14px 20px; }
</style>
