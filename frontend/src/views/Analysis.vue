<template>
  <div class="analysis-page">
    <el-card class="summary-card" shadow="never">
      <div class="card-header">
        <div class="title">
          <el-icon><DataAnalysis /></el-icon>
          <span>数据分析报告</span>
        </div>
        <el-button type="primary" size="small" @click="exportReport">
          <el-icon><Download /></el-icon>
          导出报告
        </el-button>
      </div>
      <div class="summary-content">
        <el-alert
          :title="summary"
          type="info"
          :closable="false"
          show-icon
          class="summary-alert"
        />
      </div>
    </el-card>

    <el-row :gutter="20" style="margin-top: 20px">
      <el-col :span="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><Trophy /></el-icon>
                <span>热门岗位排行 TOP10</span>
              </div>
              <el-tag type="warning" size="small">热门</el-tag>
            </div>
          </template>
          <div ref="titleChartRef" class="chart" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><Location /></el-icon>
                <span>城市岗位需求分布</span>
              </div>
              <el-tag type="primary" size="small">分布</el-tag>
            </div>
          </template>
          <div ref="cityChartRef" class="chart" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" style="margin-top: 20px">
      <el-col :span="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><Money /></el-icon>
                <span>薪资水平统计</span>
              </div>
              <el-tag type="success" size="small">薪资</el-tag>
            </div>
          </template>
          <div ref="salaryChartRef" class="chart" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><Star /></el-icon>
                <span>高频技能词 TOP12</span>
              </div>
              <el-tag type="danger" size="small">技能</el-tag>
            </div>
          </template>
          <div ref="skillChartRef" class="chart" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" style="margin-top: 20px">
      <el-col :span="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><Reading /></el-icon>
                <span>学历要求分布</span>
              </div>
              <el-tag type="info" size="small">学历</el-tag>
            </div>
          </template>
          <div ref="educationChartRef" class="chart" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><Clock /></el-icon>
                <span>工作经验要求分布</span>
              </div>
              <el-tag type="warning" size="small">经验</el-tag>
            </div>
          </template>
          <div ref="experienceChartRef" class="chart" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" style="margin-top: 20px">
      <el-col :span="24">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><OfficeBuilding /></el-icon>
                <span>热门公司招聘排行 TOP10</span>
              </div>
              <el-tag type="primary" size="small">公司</el-tag>
            </div>
          </template>
          <div ref="companyChartRef" class="chart" style="height: 400px" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import * as echarts from 'echarts';
import { ElMessage } from 'element-plus';
import {
  DataAnalysis,
  Download,
  Trophy,
  Location,
  Money,
  Star,
  Reading,
  Clock,
  OfficeBuilding
} from '@element-plus/icons-vue';
import {
  fetchAnalysisSummary,
  fetchJobStatCity,
  fetchJobStatSalaryRange,
  fetchJobStatSkill,
  fetchTopTitles,
  fetchJobStatEducation,
  fetchJobStatExperience,
  fetchJobStatCompany
} from '@/api/job';

const summary = ref('正在分析...');
const titleChartRef = ref<HTMLDivElement | null>(null);
const skillChartRef = ref<HTMLDivElement | null>(null);
const cityChartRef = ref<HTMLDivElement | null>(null);
const salaryChartRef = ref<HTMLDivElement | null>(null);
const educationChartRef = ref<HTMLDivElement | null>(null);
const experienceChartRef = ref<HTMLDivElement | null>(null);
const companyChartRef = ref<HTMLDivElement | null>(null);

// [优化] 收集所有 ECharts 实例，用于组件卸载时统一 dispose 和窗口 resize
const chartInstances: echarts.ECharts[] = [];

const init = async () => {
  try {
    const [summaryText, cityData, skillData, salaryRangeData, topTitles, educationData, experienceData, companyData] = await Promise.all([
      fetchAnalysisSummary(),
      fetchJobStatCity(),
      fetchJobStatSkill(),
      fetchJobStatSalaryRange(),
      fetchTopTitles(),
      fetchJobStatEducation(),
      fetchJobStatExperience(),
      fetchJobStatCompany()
    ]);
    
    summary.value = (summaryText as string) || '暂无数据';

    const cityColors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc'];

    if (cityChartRef.value) {
      const chart = echarts.init(cityChartRef.value);
      chartInstances.push(chart);
      chart.setOption({
        tooltip: { 
          trigger: 'item',
          formatter: '{b}: {c}个岗位 ({d}%)'
        },
        legend: {
          orient: 'vertical',
          right: 10,
          top: 'center'
        },
        series: [
          {
            name: '岗位数量',
            type: 'pie',
            radius: ['40%', '70%'],
            avoidLabelOverlap: false,
            itemStyle: {
              borderRadius: 10,
              borderColor: '#fff',
              borderWidth: 2
            },
            label: {
              show: true,
              formatter: '{b}\n{c}个'
            },
            data: (cityData as any[]).slice(0, 8).map((i: any, index: number) => ({
              name: i.name || '未知',
              value: i.count || 0,
              itemStyle: { color: cityColors[index % cityColors.length] }
            }))
          }
        ]
      });
    }

    if (titleChartRef.value) {
      const chart = echarts.init(titleChartRef.value);
      chartInstances.push(chart);
      chart.setOption({
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'shadow' }
        },
        grid: {
          left: '3%',
          right: '4%',
          bottom: '3%',
          containLabel: true
        },
        xAxis: { 
          type: 'category',
          data: (topTitles as any[]).map((i: any) => i.name).slice(0, 10),
          axisLabel: {
            interval: 0,
            rotate: 30
          }
        },
        yAxis: { type: 'value' },
        series: [{
          type: 'bar',
          data: (topTitles as any[]).map((i: any) => i.count).slice(0, 10),
          barMaxWidth: 40,
          itemStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: '#83bff6' },
              { offset: 0.5, color: '#188df0' },
              { offset: 1, color: '#188df0' }
            ]),
            borderRadius: [5, 5, 0, 0]
          }
        }]
      });
    }

    if (salaryChartRef.value) {
      const chart = echarts.init(salaryChartRef.value);
      chartInstances.push(chart);
      chart.setOption({
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'shadow' }
        },
        grid: {
          left: '3%',
          right: '4%',
          bottom: '3%',
          containLabel: true
        },
        xAxis: { 
          type: 'category',
          data: (salaryRangeData as any[]).map((i: any) => i.name).slice(0, 6)
        },
        yAxis: { type: 'value' },
        series: [{
          type: 'bar',
          data: (salaryRangeData as any[]).map((i: any) => i.count).slice(0, 6),
          barMaxWidth: 50,
          itemStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: '#91cc75' },
              { offset: 1, color: '#73c0de' }
            ]),
            borderRadius: [5, 5, 0, 0]
          }
        }]
      });
    }

    if (skillChartRef.value) {
      const chart = echarts.init(skillChartRef.value);
      chartInstances.push(chart);
      chart.setOption({
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'shadow' }
        },
        grid: {
          left: '3%',
          right: '4%',
          bottom: '3%',
          containLabel: true
        },
        xAxis: { 
          type: 'category',
          data: (skillData as any[]).map((i: any) => i.name).slice(0, 12),
          axisLabel: {
            interval: 0,
            rotate: 45
          }
        },
        yAxis: { type: 'value' },
        series: [{
          type: 'bar',
          data: (skillData as any[]).map((i: any) => i.count).slice(0, 12),
          barMaxWidth: 35,
          itemStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: '#fac858' },
              { offset: 1, color: '#ee6666' }
            ]),
            borderRadius: [5, 5, 0, 0]
          }
        }]
      });
    }

    if (educationChartRef.value) {
      const chart = echarts.init(educationChartRef.value);
      chartInstances.push(chart);
      chart.setOption({
        tooltip: {
          trigger: 'item',
          formatter: '{b}: {c}个岗位 ({d}%)'
        },
        series: [
          {
            name: '学历要求',
            type: 'pie',
            radius: '70%',
            data: (educationData as any[]).map((i: any) => ({
              name: i.name || '未知',
              value: i.count || 0
            })),
            emphasis: {
              itemStyle: {
                shadowBlur: 10,
                shadowOffsetX: 0,
                shadowColor: 'rgba(0, 0, 0, 0.5)'
              }
            }
          }
        ]
      });
    }

    if (experienceChartRef.value) {
      const chart = echarts.init(experienceChartRef.value);
      chartInstances.push(chart);
      chart.setOption({
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'shadow' }
        },
        grid: {
          left: '3%',
          right: '4%',
          bottom: '3%',
          containLabel: true
        },
        xAxis: { 
          type: 'category',
          data: (experienceData as any[]).map((i: any) => i.name)
        },
        yAxis: { type: 'value' },
        series: [{
          type: 'bar',
          data: (experienceData as any[]).map((i: any) => i.count),
          barMaxWidth: 50,
          itemStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: '#9a60b4' },
              { offset: 1, color: '#ea7ccc' }
            ]),
            borderRadius: [5, 5, 0, 0]
          }
        }]
      });
    }

    if (companyChartRef.value) {
      const chart = echarts.init(companyChartRef.value);
      chartInstances.push(chart);
      chart.setOption({
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'shadow' }
        },
        grid: {
          left: '3%',
          right: '4%',
          bottom: '3%',
          containLabel: true
        },
        xAxis: { 
          type: 'value'
        },
        yAxis: { 
          type: 'category',
          data: (companyData as any[]).map((i: any) => i.name).slice(0, 10).reverse()
        },
        series: [{
          type: 'bar',
          data: (companyData as any[]).map((i: any) => i.count).slice(0, 10).reverse(),
          barMaxWidth: 40,
          itemStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
              { offset: 0, color: '#5470c6' },
              { offset: 1, color: '#91cc75' }
            ]),
            borderRadius: [0, 5, 5, 0]
          }
        }]
      });
    }
  } catch (error) {
    summary.value = '暂无可分析数据';
  }
};

const exportReport = () => {
  ElMessage.success('报告导出功能开发中...');
};

onMounted(() => {
  init();
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
.analysis-page {
  padding: 20px;
}

.summary-card {
  margin-bottom: 20px;
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
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

.summary-content {
  margin-top: 16px;
}

.summary-alert {
  font-size: 14px;
  line-height: 1.8;
}

.chart-card {
  height: 100%;
}

.chart-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.chart-header .chart-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #333;
}

.chart {
  height: 350px;
  width: 100%;
}
</style>
