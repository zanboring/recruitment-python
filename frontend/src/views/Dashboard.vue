<template>
  <div class="dashboard">
    <!-- 统计卡片区域 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :xs="24" :sm="12" :md="6" v-for="(stat, index) in statCards" :key="index">
        <el-card class="stat-card" shadow="hover">
          <div class="stat-card-inner">
            <div class="stat-icon" :style="{ background: stat.gradient }">
              <el-icon :size="28"><component :is="stat.icon" /></el-icon>
            </div>
            <div class="stat-content">
              <div class="stat-value">
                <span class="value">{{ stat.value }}</span>
                <span class="unit" v-if="stat.unit">{{ stat.unit }}</span>
              </div>
              <div class="stat-label">{{ stat.label }}</div>
              <div class="stat-trend" v-if="stat.trend !== undefined">
                <template v-if="stat.trend !== null">
                  <el-icon :color="stat.trend > 0 ? '#67c23a' : '#f56c6c'">
                    <component :is="stat.trend > 0 ? 'ArrowUp' : 'ArrowDown'" />
                  </el-icon>
                  <span :style="{ color: stat.trend > 0 ? '#67c23a' : '#f56c6c' }">
                    {{ Math.abs(stat.trend) }}%
                  </span>
                  <span class="trend-label">较上周</span>
                </template>
                <span v-else class="trend-ready">数据已就绪</span>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 图表区域 -->
    <el-row :gutter="16" style="margin-top: 20px">
      <el-col :xs="24" :md="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><PieChart /></el-icon>
                <span>各城市岗位数量分布</span>
              </div>
              <el-tag type="info" size="small">实时</el-tag>
            </div>
          </template>
          <div ref="cityChartRef" class="chart"></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><Histogram /></el-icon>
                <span>薪资区间分布</span>
              </div>
              <el-tag type="warning" size="small">月薪</el-tag>
            </div>
          </template>
          <div ref="salaryChartRef" class="chart"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 20px">
      <el-col :xs="24" :md="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><TrendCharts /></el-icon>
                <span>技能需求排名 TOP10</span>
              </div>
              <el-tag type="success" size="small">热门</el-tag>
            </div>
          </template>
          <div ref="skillChartRef" class="chart"></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><DataAnalysis /></el-icon>
                <span>学历/经验要求分布</span>
              </div>
              <el-tag type="primary" size="small">要求</el-tag>
            </div>
          </template>
          <div ref="requirementChartRef" class="chart"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 20px">
      <el-col :span="24">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><Star /></el-icon>
                <span>热门岗位 TOP10</span>
              </div>
              <el-tag type="danger" size="small">TOP</el-tag>
            </div>
          </template>
          <div ref="titleChartRef" class="chart-lg"></div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed } from 'vue';
import { Briefcase, OfficeBuilding, Money, TrendCharts, PieChart, Histogram, DataAnalysis, Star, ArrowUp, ArrowDown } from '@element-plus/icons-vue';
import * as echarts from 'echarts';
import {
  fetchJobStatCity,
  fetchJobStatSalaryRange,
  fetchJobStatSkill,
  fetchJobStatEducation,
  fetchJobStatExperience,
  fetchJobStatStatus,
  fetchJobStatCompany,
  fetchTopTitles
} from '@/api/job';

const cityChartRef = ref<HTMLDivElement | null>(null);
const salaryChartRef = ref<HTMLDivElement | null>(null);
const skillChartRef = ref<HTMLDivElement | null>(null);
const requirementChartRef = ref<HTMLDivElement | null>(null);
const titleChartRef = ref<HTMLDivElement | null>(null);

// [优化] 收集所有 ECharts 实例，用于组件卸载时统一 dispose 和窗口 resize
const chartInstances: echarts.ECharts[] = [];

const jobCount = ref(0);
const companyCount = ref(0);
const avgSalary = ref('0');
const activeCount = ref(0);

// 统计卡片数据（趋势数据不再硬编码，仅在数据加载完成后显示）
const statCards = computed(() => [
  {
    icon: Briefcase,
    label: '岗位总数',
    value: jobCount.value,
    unit: '个',
    gradient: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    trend: jobCount.value > 0 ? null : undefined // 数据就绪前不显示趋势
  },
  {
    icon: OfficeBuilding,
    label: '企业数量',
    value: companyCount.value,
    unit: '家',
    gradient: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)',
    trend: companyCount.value > 0 ? null : undefined
  },
  {
    icon: Money,
    label: '平均薪资',
    value: avgSalary.value,
    unit: '元/月',
    gradient: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
    trend: avgSalary.value !== '0' ? null : undefined
  },
  {
    icon: TrendCharts,
    label: '在岗岗位',
    value: activeCount.value,
    unit: '个',
    gradient: 'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)',
    trend: activeCount.value > 0 ? null : undefined
  }
]);

// 渐变色配置
const gradientColors = [
  ['#667eea', '#764ba2'],
  ['#f093fb', '#f5576c'],
  ['#4facfe', '#00f2fe'],
  ['#43e97b', '#38f9d7'],
  ['#fa709a', '#fee140'],
  ['#a8edea', '#fed6e3'],
  ['#5ee7df', '#b490ca'],
  ['#d299c2', '#fef9d7'],
  ['#89f7fe', '#66a6ff'],
  ['#cd9cf2', '#f6f3ff']
];

const getGradientColor = (index: number) => {
  const colors = gradientColors[index % gradientColors.length];
  return new echarts.graphic.LinearGradient(0, 0, 1, 0, [
    { offset: 0, color: colors[0] },
    { offset: 1, color: colors[1] }
  ]);
};

const getRadialGradient = (index: number) => {
  const colors = gradientColors[index % gradientColors.length];
  return new echarts.graphic.RadialGradient(0.5, 0.5, 1, [
    { offset: 0, color: colors[0] },
    { offset: 1, color: colors[1] }
  ]);
};

const init = async () => {
  const [cityData, salaryRangeData, skillData, eduData, expData, statusData, companyData, titleData] = await Promise.all([
    fetchJobStatCity(),
    fetchJobStatSalaryRange(),
    fetchJobStatSkill(),
    fetchJobStatEducation(),
    fetchJobStatExperience(),
    fetchJobStatStatus(),
    fetchJobStatCompany(),
    fetchTopTitles()
  ]);

  // 更新统计卡片
  const allStatus = statusData || [];
  const activeStatus = allStatus.find((s: any) => s.name === 'ACTIVE');
  const newStatus = allStatus.find((s: any) => s.name === 'NEW');
  jobCount.value = (activeStatus?.count || 0) + (newStatus?.count || 0);
  activeCount.value = activeStatus?.count || 0;
  companyCount.value = (companyData || []).length;

  // 计算平均薪资
  const salarySum = (salaryRangeData || []).reduce((sum: number, item: any) => {
    return sum + ((item.avgSalary || 0) * (item.count || 0));
  }, 0);
  const totalCount = (salaryRangeData || []).reduce((sum: number, item: any) => sum + (item.count || 0), 0);
  avgSalary.value = totalCount > 0 ? Math.round(salarySum / totalCount).toLocaleString() : '0';

  // 城市分布饼图 - 带动画效果
  if (cityChartRef.value) {
    const chart = echarts.init(cityChartRef.value);
    chartInstances.push(chart);
    chart.setOption({
      tooltip: {
        trigger: 'item',
        formatter: '{b}: {c} ({d}%)',
        backgroundColor: 'rgba(255,255,255,0.95)',
        borderColor: '#f0f0f0',
        borderWidth: 1,
        padding: [12, 16],
        textStyle: { color: '#303133' },
        extraCssText: 'box-shadow: 0 4px 12px rgba(0,0,0,0.1); border-radius: 8px;'
      },
      series: [{
        type: 'pie',
        radius: ['35%', '75%'],
        center: ['50%', '55%'],
        avoidLabelOverlap: true,
        itemStyle: {
          borderRadius: 12,
          borderColor: '#fff',
          borderWidth: 3
        },
        label: {
          show: true,
          position: 'outside',
          formatter: '{b}\n{c}',
          fontSize: 12,
          color: '#606266'
        },
        labelLine: {
          show: true,
          length: 15,
          length2: 10,
          lineStyle: { width: 1, type: 'dashed' }
        },
        data: (cityData || []).map((item: any, index: number) => ({
          name: item.name || '未知',
          value: item.count || 0,
          itemStyle: { color: getRadialGradient(index) }
        })),
        animationType: 'scale',
        animationEasing: 'elasticOut',
        animationDelay: (idx: number) => idx * 80,
        animationDuration: 1500
      }]
    });
  }

  // 薪资分布柱状图
  if (salaryChartRef.value) {
    const chart = echarts.init(salaryChartRef.value);
    chartInstances.push(chart);
    chart.setOption({
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: 'rgba(255,255,255,0.95)',
        borderColor: '#f0f0f0',
        borderWidth: 1,
        padding: [12, 16],
        textStyle: { color: '#303133' },
        extraCssText: 'box-shadow: 0 4px 12px rgba(0,0,0,0.1); border-radius: 8px;'
      },
      grid: { left: '3%', right: '4%', bottom: '3%', top: '10%', containLabel: true },
      xAxis: {
        type: 'category',
        data: (salaryRangeData || []).map((i: any) => i.name || '未知'),
        axisLabel: { interval: 0, rotate: 30, fontSize: 11 },
        axisLine: { lineStyle: { color: '#e4e7ed' } },
        axisTick: { show: false }
      },
      yAxis: {
        type: 'value',
        axisLine: { show: false },
        axisLabel: { color: '#909399' },
        splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } }
      },
      series: [{
        type: 'bar',
        data: (salaryRangeData || []).map((item: any, index: number) => ({
          value: item.count || 0,
          itemStyle: {
            color: getGradientColor(index),
            borderRadius: [8, 8, 0, 0]
          }
        })),
        barMaxWidth: 50,
        emphasis: {
          itemStyle: {
            shadowBlur: 20,
            shadowOffsetX: 0,
            shadowColor: 'rgba(0,0,0,0.3)'
          }
        },
        animationDelay: (idx: number) => idx * 150,
        animationDuration: 1500,
        animationEasing: 'cubicOut'
      }]
    });
  }

  // 技能需求排名横向柱状图
  if (skillChartRef.value) {
    const chart = echarts.init(skillChartRef.value);
    chartInstances.push(chart);
    const topSkills = (skillData || []).slice(0, 10);
    chart.setOption({
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: 'rgba(255,255,255,0.95)',
        borderColor: '#f0f0f0',
        borderWidth: 1,
        padding: [12, 16],
        textStyle: { color: '#303133' },
        extraCssText: 'box-shadow: 0 4px 12px rgba(0,0,0,0.1); border-radius: 8px;'
      },
      grid: { left: '3%', right: '10%', bottom: '3%', top: '3%', containLabel: true },
      xAxis: {
        type: 'value',
        axisLine: { show: false },
        axisLabel: { color: '#909399' },
        splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } }
      },
      yAxis: {
        type: 'category',
        data: (topSkills as any[]).reverse().map((i: any) => i.name || '未知'),
        inverse: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#606266', fontSize: 12 }
      },
      series: [{
        type: 'bar',
        data: (topSkills as any[]).reverse().map((item: any, index: number) => ({
          value: item.count || 0,
          itemStyle: {
            color: getGradientColor(index),
            borderRadius: [0, 12, 12, 0]
          }
        })),
        barMaxWidth: 40,
        label: {
          show: true,
          position: 'right',
          color: '#909399',
          fontSize: 11
        },
        emphasis: {
          itemStyle: {
            shadowBlur: 20,
            shadowOffsetX: 0,
            shadowColor: 'rgba(0,0,0,0.3)'
          }
        },
        animationDelay: (idx: number) => idx * 100,
        animationDuration: 1200,
        animationEasing: 'cubicOut'
      }]
    });
  }

  // 学历/经验要求双柱状图
  if (requirementChartRef.value) {
    const chart = echarts.init(requirementChartRef.value);
    chartInstances.push(chart);
    const axis = buildAxis(eduData, expData);
    chart.setOption({
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(255,255,255,0.95)',
        borderColor: '#f0f0f0',
        borderWidth: 1,
        padding: [12, 16],
        textStyle: { color: '#303133' },
        extraCssText: 'box-shadow: 0 4px 12px rgba(0,0,0,0.1); border-radius: 8px;'
      },
      legend: {
        data: ['学历要求', '经验要求'],
        bottom: 0,
        textStyle: { color: '#606266' },
        itemWidth: 12,
        itemHeight: 12,
        itemGap: 20
      },
      grid: { left: '3%', right: '4%', bottom: '15%', top: '10%', containLabel: true },
      xAxis: {
        type: 'category',
        data: axis,
        axisLabel: { interval: 0, rotate: 30, fontSize: 11 },
        axisLine: { lineStyle: { color: '#e4e7ed' } },
        axisTick: { show: false }
      },
      yAxis: {
        type: 'value',
        axisLine: { show: false },
        axisLabel: { color: '#909399' },
        splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } }
      },
      series: [
        {
          name: '学历要求',
          type: 'bar',
          data: toSeries(axis, eduData).map((value, index) => ({
            value,
            itemStyle: {
              color: getGradientColor(0),
              borderRadius: [6, 6, 0, 0]
            }
          })),
          barMaxWidth: 35,
          animationDelay: (idx: number) => idx * 120
        },
        {
          name: '经验要求',
          type: 'bar',
          data: toSeries(axis, expData).map((value, index) => ({
            value,
            itemStyle: {
              color: getGradientColor(2),
              borderRadius: [6, 6, 0, 0]
            }
          })),
          barMaxWidth: 35,
          animationDelay: (idx: number) => idx * 120 + 300
        }
      ],
      animationDuration: 1500,
      animationEasing: 'cubicOut'
    });
  }

  // 热门岗位TOP10
  if (titleChartRef.value) {
    const chart = echarts.init(titleChartRef.value);
    chartInstances.push(chart);
    chart.setOption({
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: 'rgba(255,255,255,0.95)',
        borderColor: '#f0f0f0',
        borderWidth: 1,
        padding: [12, 16],
        textStyle: { color: '#303133' },
        extraCssText: 'box-shadow: 0 4px 12px rgba(0,0,0,0.1); border-radius: 8px;'
      },
      grid: { left: '3%', right: '10%', bottom: '3%', top: '3%', containLabel: true },
      xAxis: {
        type: 'value',
        axisLine: { show: false },
        axisLabel: { color: '#909399' },
        splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } }
      },
      yAxis: {
        type: 'category',
        data: (titleData || []).reverse().map((i: any) => i.name || '未知'),
        inverse: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#606266', fontSize: 12 }
      },
      series: [{
        type: 'bar',
        data: (titleData || []).reverse().map((item: any, index: number) => ({
          value: item.count || 0,
          itemStyle: {
            color: getGradientColor(index),
            borderRadius: [0, 12, 12, 0]
          }
        })),
        barMaxWidth: 50,
        label: {
          show: true,
          position: 'right',
          color: '#909399',
          fontSize: 11
        },
        emphasis: {
          itemStyle: {
            shadowBlur: 20,
            shadowOffsetX: 0,
            shadowColor: 'rgba(0,0,0,0.3)'
          }
        },
        animationDelay: (idx: number) => idx * 100,
        animationDuration: 1500,
        animationEasing: 'cubicOut'
      }]
    });
  }
};

const buildAxis = (a: any[], b: any[]) => {
  const set = new Set<string>();
  (a || []).forEach((i: any) => set.add(i.name || '未知'));
  (b || []).forEach((i: any) => set.add(i.name || '未知'));
  return Array.from(set);
};

const toSeries = (axis: string[], source: any[]) => {
  const map = new Map<string, number>();
  (source || []).forEach((i: any) => map.set(i.name || '未知', i.count || 0));
  return axis.map((key) => map.get(key) || 0);
};

onMounted(() => {
  init().catch(() => undefined);
  // [优化] 监听窗口 resize，自动调整所有图表大小
  window.addEventListener('resize', handleChartsResize);
});

// [优化] 窗口 resize 处理函数
const handleChartsResize = () => {
  chartInstances.forEach(chart => {
    if (chart && !chart.isDisposed()) {
      chart.resize();
    }
  });
};

// [优化] 组件卸载时释放所有图表实例，防止内存泄漏
onUnmounted(() => {
  window.removeEventListener('resize', handleChartsResize);
  chartInstances.forEach(chart => {
    if (chart && !chart.isDisposed()) {
      chart.dispose();
    }
  });
  chartInstances.length = 0;
});
</script>

<style scoped>
.dashboard {
  padding: 8px;
}

/* 统计卡片样式 */
.stat-row {
  margin-bottom: 8px;
}

.stat-card {
  border: none;
  border-radius: 16px;
  overflow: hidden;
  transition: all 0.3s ease;
}

.stat-card:hover {
  transform: translateY(-6px);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.12) !important;
}

.stat-card :deep(.el-card__body) {
  padding: 20px;
}

.stat-card-inner {
  display: flex;
  align-items: center;
  gap: 16px;
}

.stat-icon {
  width: 64px;
  height: 64px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.15);
}

.stat-content {
  flex: 1;
  min-width: 0;
}

.stat-value {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.stat-value .value {
  font-size: 28px;
  font-weight: 700;
  color: #303133;
  line-height: 1.2;
}

.stat-value .unit {
  font-size: 14px;
  color: #909399;
}

.stat-label {
  font-size: 14px;
  color: #909399;
  margin-top: 4px;
}

.stat-trend {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  font-size: 12px;
}

.trend-label {
  color: #c0c4cc;
  margin-left: 4px;
}

.trend-ready {
  color: #67c23a;
  font-size: 12px;
}

/* 图表卡片样式 */
.chart-card {
  border: none;
  border-radius: 16px;
  transition: all 0.3s ease;
}

.chart-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.08) !important;
}

.chart-card :deep(.el-card__header) {
  padding: 16px 20px;
  border-bottom: 1px solid #f0f0f0;
}

.chart-card :deep(.el-card__body) {
  padding: 16px;
}

.chart-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.chart-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.chart-title .el-icon {
  font-size: 18px;
  color: #409eff;
}

.chart {
  height: 320px;
}

.chart-lg {
  height: 380px;
}

/* ===== 毕业设计风格增强样式 ===== */
.dashboard :deep(.el-row) {
  margin-left: 0 !important;
  margin-right: 0 !important;
}

.dashboard :deep(.el-col) {
  padding-left: 10px !important;
  padding-right: 10px !important;
}

/* 统计卡片增强 */
.dashboard :deep(.stat-card) {
  background: linear-gradient(135deg, #ffffff 0%, #f8f9fc 100%);
  border: 1px solid rgba(233, 236, 241, 0.8) !important;
}

.dashboard :deep(.stat-card .el-card__body) {
  background: transparent !important;
}

/* 图表卡片增强 */
.dashboard :deep(.chart-card) {
  background: #fff;
  border: 1px solid rgba(233, 236, 241, 0.8) !important;
}

.dashboard :deep(.chart-card .el-card__header) {
  background: linear-gradient(135deg, #fafbfc 0%, #f4f6f9 100%) !important;
  border-bottom: 1px solid #f0f0f0 !important;
}

.dashboard :deep(.el-tag) {
  border-radius: 20px !important;
  padding: 0 12px !important;
  font-weight: 500 !important;
}

.dashboard :deep(.el-tag--info) {
  background: linear-gradient(135deg, #e9e9e9 0%, #d9d9d9 100%) !important;
  color: #606266 !important;
  border: none !important;
}

.dashboard :deep(.el-tag--warning) {
  background: linear-gradient(135deg, #fdf6ec 0%, #faecd8 100%) !important;
  color: #e6a23c !important;
  border: none !important;
}

.dashboard :deep(.el-tag--success) {
  background: linear-gradient(135deg, #f0f9eb 0%, #e1f3d8 100%) !important;
  color: #67c23a !important;
  border: none !important;
}

.dashboard :deep(.el-tag--primary) {
  background: linear-gradient(135deg, #ecf5ff 0%, #d9ecff 100%) !important;
  color: #409eff !important;
  border: none !important;
}

.dashboard :deep(.el-tag--danger) {
  background: linear-gradient(135deg, #fef0f0 0%, #ffe1e1 100%) !important;
  color: #f56c6c !important;
  border: none !important;
}

/* 标题图标增强 */
.dashboard :deep(.chart-title .el-icon) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

/* 趋势箭头增强 */
.dashboard :deep(.stat-trend .el-icon) {
  font-size: 14px !important;
}

/* 输入框聚焦样式 - 全局应用 */
.dashboard :deep(.el-input__wrapper) {
  border-radius: 10px !important;
  box-shadow: 0 0 0 1px #dcdfe6 inset !important;
  transition: all 0.3s ease !important;
}

.dashboard :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px #c0c4cc inset !important;
}

.dashboard :deep(.el-input__wrapper:focus-within) {
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.2) !important;
  border-color: #667eea !important;
}

/* 按钮悬停效果 */
.dashboard :deep(.el-button--primary) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
  border: none !important;
  border-radius: 10px !important;
}

.dashboard :deep(.el-button--primary:hover) {
  background: linear-gradient(135deg, #5a6fd6 0%, #6a4190 100%) !important;
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4) !important;
}

/* 分页器增强 */
.dashboard :deep(.el-pagination) {
  margin-top: 20px !important;
  justify-content: center !important;
}

.dashboard :deep(.el-pagination .el-pager li) {
  border-radius: 8px !important;
  margin: 0 4px !important;
}

.dashboard :deep(.el-pagination .el-pager li:hover) {
  color: #667eea !important;
}

.dashboard :deep(.el-pagination .el-pager li.is-active) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
  color: #fff !important;
}
</style>