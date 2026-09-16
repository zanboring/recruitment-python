<template>
  <div class="compare-page">
    <el-card class="filter-card" shadow="never">
      <div class="card-header">
        <div class="title">
          <el-icon><TrendCharts /></el-icon>
          <span>数据对比分析</span>
        </div>
        <el-button type="primary" size="small" @click="refreshData">
          <el-icon><Refresh /></el-icon>
          刷新数据
        </el-button>
      </div>

      <div class="filter-section">
        <el-row :gutter="16">
          <el-col :span="6">
            <el-select v-model="compareType" placeholder="选择对比维度" size="large" @change="handleCompareTypeChange">
              <el-option label="岗位状态对比" value="status" />
              <el-option label="城市薪资对比" value="city" />
              <el-option label="学历薪资对比" value="education" />
              <el-option label="经验薪资对比" value="experience" />
            </el-select>
          </el-col>
          <el-col :span="6">
            <el-select v-model="chartType" placeholder="选择图表类型" size="large" @change="updateChart">
              <el-option label="柱状图" value="bar" />
              <el-option label="饼图" value="pie" />
              <el-option label="折线图" value="line" />
              <el-option label="雷达图" value="radar" />
            </el-select>
          </el-col>
        </el-row>
      </div>
    </el-card>

    <el-row :gutter="20" style="margin-top: 20px">
      <el-col :span="16">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><DataAnalysis /></el-icon>
                <span>{{ getChartTitle() }}</span>
              </div>
              <el-tag type="info" size="small">{{ chartType === 'bar' ? '柱状图' : chartType === 'pie' ? '饼图' : chartType === 'line' ? '折线图' : '雷达图' }}</el-tag>
            </div>
          </template>
          <div ref="mainChartRef" class="chart" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card class="stat-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><DataBoard /></el-icon>
                <span>数据统计</span>
              </div>
            </div>
          </template>
          <div class="stat-content">
            <div class="stat-item" v-for="(item, index) in statItems" :key="index">
              <div class="stat-label">{{ item.label }}</div>
              <div class="stat-value" :style="{ color: item.color }">{{ item.value }}</div>
              <div class="stat-desc">{{ item.desc }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" style="margin-top: 20px">
      <el-col :span="24">
        <el-card class="table-card" shadow="hover">
          <template #header>
            <div class="chart-header">
              <div class="chart-title">
                <el-icon><List /></el-icon>
                <span>详细数据</span>
              </div>
              <el-button type="primary" size="small" @click="exportData">
                <el-icon><Download /></el-icon>
                导出数据
              </el-button>
            </div>
          </template>
          <el-table :data="tableData" stripe>
            <el-table-column prop="name" label="名称" width="200" />
            <el-table-column prop="count" label="数量" width="120" sortable />
            <el-table-column prop="avgSalary" label="平均薪资" width="150" sortable>
              <template #default="{ row }">
                {{ formatSalary(row.avgSalary) }}
              </template>
            </el-table-column>
            <el-table-column prop="percentage" label="占比" width="120">
              <template #default="{ row }">
                {{ row.percentage }}%
              </template>
            </el-table-column>
            <el-table-column label="趋势" width="200">
              <template #default="{ row }">
                <el-progress :percentage="row.percentage" :color="getProgressColor(row.percentage)" />
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, computed } from 'vue';
import * as echarts from 'echarts';
import { ElMessage } from 'element-plus';
import {
  TrendCharts,
  Refresh,
  DataAnalysis,
  DataBoard,
  List,
  Download
} from '@element-plus/icons-vue';
import {
  fetchJobStatStatus,
  fetchJobStatCity,
  fetchJobStatEducation,
  fetchJobStatExperience
} from '@/api/job';

const compareType = ref('status');
const chartType = ref('bar');
const mainChartRef = ref<HTMLDivElement | null>(null);
let chartInstance: echarts.ECharts | null = null;

const tableData = ref<any[]>([]);

const statItems = computed(() => {
  const total = tableData.value.reduce((sum, item) => sum + (item.count || 0), 0);
  const avgSalary = tableData.value.length > 0 
    ? tableData.value.reduce((sum, item) => sum + (item.avgSalary || 0), 0) / tableData.value.length 
    : 0;
  const maxCount = Math.max(...tableData.value.map(item => item.count || 0), 0);
  const maxSalary = Math.max(...tableData.value.map(item => item.avgSalary || 0), 0);

  return [
    { label: '总数量', value: total, desc: '个岗位', color: '#5470c6' },
    { label: '平均薪资', value: formatSalary(avgSalary), desc: '月薪', color: '#91cc75' },
    { label: '最高数量', value: maxCount, desc: '个岗位', color: '#fac858' },
    { label: '最高薪资', value: formatSalary(maxSalary), desc: '月薪', color: '#ee6666' }
  ];
});

const labelMap: Record<string, string> = {
  NEW: '新增岗位',
  ACTIVE: '正常在岗',
  OFFLINE: '已下架'
};

const getChartTitle = () => {
  const titles: Record<string, string> = {
    status: '岗位状态分布',
    city: '城市薪资对比',
    education: '学历薪资对比',
    experience: '经验薪资对比'
  };
  return titles[compareType.value] || '数据对比';
};

const formatSalary = (salary?: number) => {
  if (!salary) return '0K';
  return `${Math.round(salary)}K`;
};

const getProgressColor = (percentage: number) => {
  if (percentage >= 30) return '#67c23a';
  if (percentage >= 15) return '#e6a23c';
  return '#f56c6c';
};

const loadData = async () => {
  try {
    let data: any[] = [];
    
    switch (compareType.value) {
      case 'status':
        data = await fetchJobStatStatus() as any[];
        break;
      case 'city':
        data = await fetchJobStatCity() as any[];
        break;
      case 'education':
        data = await fetchJobStatEducation() as any[];
        break;
      case 'experience':
        data = await fetchJobStatExperience() as any[];
        break;
    }

    const total = data.reduce((sum, item) => sum + (item.count || 0), 0);
    
    tableData.value = data.map((item) => ({
      ...item,
      name: labelMap[item.name] || item.name,
      percentage: total > 0 ? Number(((item.count || 0) / total * 100).toFixed(2)) : 0
    }));

    updateChart();
  } catch (error) {
    ElMessage.error('加载数据失败');
  }
};

const updateChart = () => {
  if (!mainChartRef.value) return;

  if (chartInstance) {
    chartInstance.dispose();
  }

  chartInstance = echarts.init(mainChartRef.value);

  const data = tableData.value;
  const colors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4'];

  let option: any = {
    tooltip: {
      trigger: chartType.value === 'pie' ? 'item' : 'axis'
    },
    legend: chartType.value === 'pie' ? {
      orient: 'vertical',
      right: 10,
      top: 'center'
    } : undefined
  };

  if (chartType.value === 'pie') {
    option.series = [{
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
        formatter: '{b}\n{c}个\n({d}%)'
      },
      data: data.map((item, index) => ({
        name: item.name,
        value: item.count || 0,
        itemStyle: { color: colors[index % colors.length] }
      }))
    }];
  } else if (chartType.value === 'bar') {
    option.xAxis = {
      type: 'category',
      data: data.map(item => item.name),
      axisLabel: {
        interval: 0,
        rotate: data.length > 6 ? 30 : 0
      }
    };
    option.yAxis = { type: 'value' };
    option.series = [{
      type: 'bar',
      data: data.map((item, index) => ({
        value: item.count || 0,
        itemStyle: { color: colors[index % colors.length] }
      })),
      barMaxWidth: 50,
      itemStyle: {
        borderRadius: [5, 5, 0, 0]
      }
    }];
  } else if (chartType.value === 'line') {
    option.xAxis = {
      type: 'category',
      data: data.map(item => item.name),
      boundaryGap: false
    };
    option.yAxis = { type: 'value' };
    option.series = [{
      type: 'line',
      data: data.map(item => item.count || 0),
      smooth: true,
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(84, 112, 198, 0.3)' },
          { offset: 1, color: 'rgba(84, 112, 198, 0.05)' }
        ])
      },
      itemStyle: { color: '#5470c6' }
    }];
  } else if (chartType.value === 'radar') {
    option.radar = {
      indicator: data.map(item => ({
        name: item.name,
        max: Math.max(...data.map(d => d.count || 0))
      }))
    };
    option.series = [{
      type: 'radar',
      data: [{
        value: data.map(item => item.count || 0),
        name: '数量分布',
        areaStyle: {
          color: 'rgba(84, 112, 198, 0.3)'
        }
      }]
    }];
  }

  chartInstance.setOption(option);
};

const handleCompareTypeChange = () => {
  loadData();
};

const refreshData = () => {
  loadData();
  ElMessage.success('数据已刷新');
};

const exportData = () => {
  ElMessage.success('数据导出功能开发中...');
};

onMounted(() => {
  loadData();
  // [优化] 监听窗口 resize，自动调整图表大小
  window.addEventListener('resize', handleChartResize);
});

// [优化] 窗口 resize 处理函数
const handleChartResize = () => {
  if (chartInstance && !chartInstance.isDisposed()) {
    chartInstance.resize();
  }
};

// [优化] 组件卸载时释放图表实例，防止内存泄漏
onUnmounted(() => {
  window.removeEventListener('resize', handleChartResize);
  if (chartInstance && !chartInstance.isDisposed()) {
    chartInstance.dispose();
    chartInstance = null;
  }
});
</script>

<style scoped>
.compare-page {
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
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

.filter-section {
  margin-top: 16px;
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
  height: 400px;
  width: 100%;
}

.stat-card {
  height: 100%;
}

.stat-content {
  padding: 10px 0;
}

.stat-item {
  padding: 20px;
  border-bottom: 1px solid #f0f0f0;
}

.stat-item:last-child {
  border-bottom: none;
}

.stat-label {
  font-size: 14px;
  color: #666;
  margin-bottom: 8px;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  margin-bottom: 4px;
}

.stat-desc {
  font-size: 12px;
  color: #999;
}
</style>
