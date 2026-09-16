<template>
  <div class="home-container">
    <!-- 欢迎卡片 -->
    <el-card class="welcome-card" shadow="hover">
      <div class="welcome-content">
        <div class="welcome-header">
          <div class="welcome-icon">
            <el-icon :size="64" color="#fff"><DataBoard /></el-icon>
          </div>
          <div class="welcome-text">
            <h1 class="welcome-title">欢迎使用 Recruitment</h1>
            <p class="welcome-desc">基于 SpringBoot + Vue3 构建的智能化招聘数据分析平台</p>
          </div>
        </div>

        <el-divider />

        <!-- 功能特色 -->
        <el-row :gutter="20" class="feature-list">
          <el-col :span="6" v-for="(feature, index) in features" :key="feature.title">
            <div class="feature-item" :style="{ animationDelay: index * 0.1 + 's' }">
              <div class="feature-icon" :style="{ background: feature.color }">
                <el-icon :size="28" color="#fff"><component :is="feature.icon" /></el-icon>
              </div>
              <div class="feature-info">
                <h4>{{ feature.title }}</h4>
                <p>{{ feature.desc }}</p>
              </div>
            </div>
          </el-col>
        </el-row>

        <el-divider />

        <!-- 快速操作 -->
        <div class="quick-actions">
          <h3><el-icon><Lightning /></el-icon>快速开始</h3>
          <el-space size="large">
            <el-button type="primary" size="large" class="quick-btn" @click="goTo('/jobs')">
              <el-icon><Search /></el-icon>查看岗位
            </el-button>
            <el-button type="success" size="large" class="quick-btn" @click="goTo('/dashboard')">
              <el-icon><DataAnalysis /></el-icon>数据看板
            </el-button>
            <el-button type="warning" size="large" class="quick-btn" @click="goTo('/data')">
              <el-icon><Upload /></el-icon>数据导入
            </el-button>
            <el-button type="info" size="large" class="quick-btn" @click="goTo('/analysis')">
              <el-icon><PieChart /></el-icon>数据分析
            </el-button>
          </el-space>
        </div>
      </div>
    </el-card>

    <!-- 信息卡片 -->
    <el-row :gutter="20" style="margin-top: 20px">
      <el-col :span="8">
        <el-card shadow="hover" class="info-card">
          <template #header>
            <div class="card-header">
              <el-icon class="header-icon" style="color: #409eff"><InfoFilled /></el-icon>
              <span>系统说明</span>
            </div>
          </template>
          <ul class="info-list">
            <li>自动爬取BOSS直聘、智联招聘、前程无忧、猎聘四大平台</li>
            <li>每日定时更新岗位数据</li>
            <li>多维度筛选与智能分析</li>
            <li>AI智能推荐优质岗位</li>
          </ul>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover" class="info-card">
          <template #header>
            <div class="card-header">
              <el-icon class="header-icon" style="color: #e6a23c"><WarningFilled /></el-icon>
              <span>使用提示</span>
            </div>
          </template>
          <ul class="info-list">
            <li>首次使用请先执行数据爬取</li>
            <li>岗位列表支持多维度筛选</li>
            <li>数据看板实时展示统计图表</li>
            <li>AI分析基于筛选结果智能生成</li>
          </ul>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover" class="info-card">
          <template #header>
            <div class="card-header">
              <el-icon class="header-icon" style="color: #67c23a"><CircleCheckFilled /></el-icon>
              <span>技术栈</span>
            </div>
          </template>
          <ul class="info-list">
            <li>后端：SpringBoot 3.2 + MyBatis</li>
            <li>前端：Vue 3 + TypeScript + Element Plus</li>
            <li>图表：ECharts 5</li>
            <li>爬虫：Jsoup + 定时任务</li>
          </ul>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router';
import { DataBoard, Search, DataAnalysis, Upload, PieChart, InfoFilled, WarningFilled, CircleCheckFilled, Briefcase, TrendCharts, Lightning } from '@element-plus/icons-vue';

const router = useRouter();

const goTo = (path: string) => {
  router.push(path);
};

const features = [
  {
    title: '智能爬虫',
    desc: '四大平台全行业数据自动采集',
    icon: Briefcase,
    color: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
  },
  {
    title: '多维筛选',
    desc: '岗位/城市/薪资/学历/经验多维度精准筛选',
    icon: Search,
    color: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)'
  },
  {
    title: '数据可视化',
    desc: 'ECharts图表实时展示统计分析',
    icon: PieChart,
    color: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)'
  },
  {
    title: 'AI智能分析',
    desc: '基于筛选结果智能分析推荐',
    icon: TrendCharts,
    color: 'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)'
  }
];
</script>

<style scoped>
.home-container {
  padding: 16px;
}

.welcome-card {
  border: none;
  border-radius: 20px;
  background: linear-gradient(135deg, #fff 0%, #f8faff 100%);
  overflow: hidden;
}

.welcome-card :deep(.el-card__body) {
  padding: 32px;
}

.welcome-content {
  padding: 8px;
}

.welcome-header {
  display: flex;
  align-items: center;
  gap: 24px;
  margin-bottom: 16px;
}

.welcome-icon {
  width: 100px;
  height: 100px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 12px 40px rgba(102, 126, 234, 0.4);
  animation: float 3s ease-in-out infinite;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}

.welcome-text {
  flex: 1;
}

.welcome-title {
  font-size: 32px;
  font-weight: 700;
  color: #303133;
  margin: 0 0 8px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.welcome-desc {
  font-size: 16px;
  color: #909399;
  margin: 0;
}

.feature-list {
  margin: 24px 0;
}

.feature-item {
  display: flex;
  align-items: center;
  padding: 20px;
  background: #fff;
  border-radius: 16px;
  border: 1px solid #f0f0f0;
  transition: all 0.3s ease;
  animation: fadeInUp 0.5s ease-out forwards;
  opacity: 0;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.feature-item:hover {
  transform: translateY(-6px);
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.08);
  border-color: transparent;
}

.feature-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 14px;
  flex-shrink: 0;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.1);
}

.feature-info h4 {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.feature-info p {
  margin: 0;
  font-size: 13px;
  color: #909399;
}

.quick-actions {
  padding: 16px 0;
}

.quick-actions h3 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
  color: #303133;
  font-size: 16px;
}

.quick-actions h3 .el-icon {
  color: #e6a23c;
}

.quick-btn {
  padding: 16px 28px;
  font-weight: 600;
  border-radius: 12px;
  transition: all 0.3s ease;
}

.quick-btn:hover {
  transform: translateY(-3px);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.15);
}

.info-card {
  border: none;
  border-radius: 16px;
  transition: all 0.3s ease;
}

.info-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.08) !important;
}

.info-card :deep(.el-card__header) {
  padding: 16px 20px;
  border-bottom: 1px solid #f0f0f0;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 15px;
}

.header-icon {
  font-size: 18px;
}

.info-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.info-list li {
  padding: 10px 0;
  color: #606266;
  font-size: 14px;
  position: relative;
  padding-left: 20px;
  border-bottom: 1px dashed #f0f0f0;
}

.info-list li:last-child {
  border-bottom: none;
}

.info-list li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 16px;
  width: 8px;
  height: 8px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 50%;
}

/* ========== 毕业设计级UI增强 ========== */
.home-container :deep(.el-card) {
  border: none;
}

.home-container :deep(.el-card__header) {
  border-bottom: 1px solid #f0f0f0;
  font-weight: 600;
}

.home-container :deep(.el-button) {
  border-radius: 10px;
  font-weight: 500;
  transition: all 0.3s ease;
}

.home-container :deep(.el-button--primary) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
}

.home-container :deep(.el-button--success) {
  background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%);
  border: none;
}

.home-container :deep(.el-button--warning) {
  background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
  border: none;
}

.home-container :deep(.el-button--info) {
  background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
  border: none;
}

.home-container :deep(.el-divider__text) {
  background: #f5f7fa;
  color: #909399;
  font-weight: 500;
}

.home-container :deep(.el-space) {
  display: flex;
  justify-content: center;
}

.home-container :deep(.el-col) {
  margin-bottom: 16px;
}
</style>