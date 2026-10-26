<template>
  <el-container class="layout">
    <!-- 左侧导航 -->
    <el-aside class="sidebar">
      <div class="sidebar-header">
        <div class="logo-wrapper">
          <div class="logo-icon">
            <el-icon :size="28"><DataBoard /></el-icon>
          </div>
          <div class="logo-text">
            <span class="title">Recruitment</span>
            <span class="subtitle">招聘可视化</span>
          </div>
        </div>
      </div>

      <el-menu
        :default-active="active"
        router
        class="sidebar-menu"
        :collapse="isCollapse"
        background-color="transparent"
        text-color="rgba(255, 255, 255, 0.7)"
        active-text-color="#fff"
      >
        <el-menu-item index="/">
          <el-icon><HomeFilled /></el-icon>
          <template #title>
            <span class="menu-text">首页</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/dashboard">
          <el-icon><DataAnalysis /></el-icon>
          <template #title>
            <span class="menu-text">数据可视化</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/jobs">
          <el-icon><Briefcase /></el-icon>
          <template #title>
            <span class="menu-text">岗位列表</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/compare">
          <el-icon><TrendCharts /></el-icon>
          <template #title>
            <span class="menu-text">数据对比</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/analysis">
          <el-icon><PieChart /></el-icon>
          <template #title>
            <span class="menu-text">数据分析</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/data">
          <el-icon><FolderOpened /></el-icon>
          <template #title>
            <span class="menu-text">数据管理</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/skill-profile">
          <el-icon><UserFilled /></el-icon>
          <template #title>
            <span class="menu-text">技能画像</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <template #title>
            <span class="menu-text">系统设置</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/ai">
          <el-icon><ChatDotRound /></el-icon>
          <template #title>
            <span class="menu-text">AI智能分析</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/report">
          <el-icon><Document /></el-icon>
          <template #title>
            <span class="menu-text">自动化日报</span>
          </template>
        </el-menu-item>
        <el-sub-menu v-if="isAdmin" index="admin">
          <template #title>
            <el-icon><Setting /></el-icon>
            <span class="menu-text">系统管理</span>
          </template>
          <el-menu-item index="/admin/users">
            <el-icon><User /></el-icon>
            <template #title>用户管理</template>
          </el-menu-item>
          <el-menu-item index="/admin/logs">
            <el-icon><Files /></el-icon>
            <template #title>操作日志</template>
          </el-menu-item>
          <el-menu-item index="/admin/knowledge">
            <el-icon><Notebook /></el-icon>
            <template #title>知识库管理</template>
          </el-menu-item>
          <el-menu-item index="/admin/models">
            <el-icon><Cpu /></el-icon>
            <template #title>模型管理</template>
          </el-menu-item>
        </el-sub-menu>
      </el-menu>

      <div class="sidebar-footer">
        <div class="user-info">
          <el-avatar :size="36" style="background: linear-gradient(135deg, #667eea, #764ba2)">
            {{ username?.charAt(0)?.toUpperCase() || 'U' }}
          </el-avatar>
          <div class="user-detail">
            <span class="username">{{ username || '用户' }}</span>
            <span class="role">管理员</span>
          </div>
        </div>
      </div>
    </el-aside>

    <!-- 右侧内容 -->
    <el-container class="main-container">
      <el-header class="header">
        <div class="header-left">
          <div class="breadcrumb">
            <el-breadcrumb separator="/">
              <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
              <el-breadcrumb-item v-if="currentRoute">{{ currentRoute }}</el-breadcrumb-item>
            </el-breadcrumb>
          </div>
        </div>
        <div class="header-right">
          <div class="header-time">{{ currentTime }}</div>
          <el-dropdown @command="handleCommand">
            <div class="user-dropdown">
              <el-avatar :size="32" style="background: linear-gradient(135deg, #667eea, #764ba2)">
                {{ username?.charAt(0)?.toUpperCase() || 'U' }}
              </el-avatar>
              <span class="username-text">{{ username || '用户' }}</span>
              <el-icon><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon>个人中心
                </el-dropdown-item>
                <el-dropdown-item command="settings">
                  <el-icon><Setting /></el-icon>系统设置
                </el-dropdown-item>
                <el-dropdown-item command="logout" divided>
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="main-content">
        <router-view v-slot="{ Component }">
          <transition name="fade-slide" mode="out-in">
            <Suspense>
              <template #default>
                <component :is="Component" />
              </template>
              <template #fallback>
                <div class="page-loading">
                  <div class="loading-spinner">
                    <el-icon class="is-loading" :size="40" color="#667eea"><Loading /></el-icon>
                    <p>加载中...</p>
                  </div>
                </div>
              </template>
            </Suspense>
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useUserStore } from '@/store/user';
import {
  HomeFilled,
  DataAnalysis,
  Briefcase,
  TrendCharts,
  PieChart,
  FolderOpened,
  Setting,
  SwitchButton,
  DataBoard,
  ArrowDown,
  User,
  ChatDotRound,
  Loading,
  Files,
  Notebook,
  Cpu,
  Document,
  UserFilled
} from '@element-plus/icons-vue';

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();
userStore.loadFromStorage();

const isCollapse = ref(false);
const currentTime = ref('');

const active = computed(() => route.path);
const username = computed(() => userStore.user?.username || '');
const isAdmin = computed(() => userStore.role === 'ADMIN');

const currentRoute = computed(() => {
  const routeNames: Record<string, string> = {
    '/': '首页',
    '/dashboard': '数据可视化',
    '/jobs': '岗位列表',
    '/compare': '数据对比',
    '/analysis': '数据分析',
    '/data': '数据管理',
    '/skill-profile': '技能画像',
    '/settings': '系统设置',
    '/ai': 'AI智能分析',
    '/report': '自动化日报',
    '/admin/users': '用户管理',
    '/admin/logs': '操作日志',
    '/admin/knowledge': '知识库管理',
    '/admin/models': '模型管理'
  };
  return routeNames[route.path] || '';
});

const handleCommand = (command: string) => {
  if (command === 'logout') {
    userStore.logout();
    router.push('/login');
  } else if (command === 'settings') {
    router.push('/settings');
  }
};

const updateTime = () => {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  const hour = String(now.getHours()).padStart(2, '0');
  const minute = String(now.getMinutes()).padStart(2, '0');
  currentTime.value = `${year}-${month}-${day} ${hour}:${minute}`;
};

let timer: number;

onMounted(() => {
  updateTime();
  timer = window.setInterval(updateTime, 60000);
});

onUnmounted(() => {
  clearInterval(timer);
});
</script>

<style scoped>
.layout {
  height: 100vh;
  background: #f0f2f5;
}

/* 侧边栏样式 */
.sidebar {
  width: 240px !important;
  background: linear-gradient(180deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
  display: flex;
  flex-direction: column;
  box-shadow: 4px 0 20px rgba(0, 0, 0, 0.15);
  overflow: hidden;
}

.sidebar-header {
  padding: 20px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.logo-wrapper {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo-icon {
  width: 44px;
  height: 44px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
}

.logo-text {
  display: flex;
  flex-direction: column;
}

.logo-text .title {
  font-size: 16px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 1px;
}

.logo-text .subtitle {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.6);
}

/* 菜单样式 */
.sidebar-menu {
  flex: 1;
  border-right: none;
  padding: 12px 8px;
  overflow-y: auto;
}

.sidebar-menu :deep(.el-menu-item) {
  height: 48px;
  margin: 4px 0;
  border-radius: 10px;
  transition: all 0.3s ease;
}

.sidebar-menu :deep(.el-menu-item:hover) {
  background: rgba(255, 255, 255, 0.1) !important;
}

.sidebar-menu :deep(.el-menu-item.is-active) {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.8), rgba(118, 75, 162, 0.8)) !important;
}

.sidebar-menu :deep(.el-menu-item.is-active::before) {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 4px;
  height: 24px;
  background: #fff;
  border-radius: 0 4px 4px 0;
}

.menu-text {
  font-size: 14px;
  font-weight: 500;
  margin-left: 8px;
}

/* 侧边栏底部 */
.sidebar-footer {
  padding: 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.user-info {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 12px;
}

.user-detail {
  display: flex;
  flex-direction: column;
}

.user-detail .username {
  font-size: 14px;
  font-weight: 600;
  color: #fff;
}

.user-detail .role {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.5);
}

/* 主容器 */
.main-container {
  display: flex;
  flex-direction: column;
}

/* 头部样式 */
.header {
  height: 64px !important;
  background: #fff;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 24px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  border-bottom: 1px solid #f0f0f0;
}

.header-left {
  display: flex;
  align-items: center;
}

.breadcrumb :deep(.el-breadcrumb__item) {
  font-size: 14px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 20px;
}

.header-time {
  font-size: 13px;
  color: #909399;
}

.user-dropdown {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 6px 12px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.user-dropdown:hover {
  background: #f5f7fa;
}

.username-text {
  font-size: 14px;
  color: #303133;
  font-weight: 500;
}

/* 内容区域 */
.main-content {
  padding: 20px;
  background: #f0f2f5;
  min-height: calc(100vh - 64px);
}

/* 过渡动画 */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.3s ease;
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(10px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}

/* ========== 毕业设计级UI增强 ========== */
.layout :deep(.el-aside) {
  overflow: hidden;
}

.layout :deep(.el-menu-item) {
  position: relative;
  transition: all 0.3s ease;
}

.layout :deep(.el-menu-item:hover) {
  transform: translateX(4px);
}

.layout :deep(.el-dropdown-menu__item) {
  border-radius: 8px;
  margin: 4px 8px;
  padding: 8px 16px;
}

.layout :deep(.el-dropdown-menu__item:hover) {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.1), rgba(118, 75, 162, 0.1)) !important;
}

.layout :deep(.el-breadcrumb__inner) {
  font-weight: 500;
}

.layout :deep(.el-breadcrumb__inner.is-link:hover) {
  color: #667eea !important;
}

.layout :deep(.el-header) {
  border-bottom: 1px solid #f0f0f0;
}

.layout :deep(.el-main) {
  background: #f5f7fa;
}

/* 页面加载状态 */
.page-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 400px;
}

.loading-spinner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.loading-spinner p {
  color: #909399;
  font-size: 14px;
  margin: 0;
}
</style>