<template>
  <router-view v-slot="{ Component, route }">
    <keep-alive :include="['AIChat']">
      <component :is="Component" :key="route.path" />
    </keep-alive>
  </router-view>
</template>

<script setup lang="ts"></script>

<style>
/* 全局样式配置 */
:root {
  /* 主色调 */
  --primary-color: #667eea;
  --primary-light: #764ba2;
  --success-color: #43e97b;
  --warning-color: #f093fb;
  --danger-color: #f5576c;
  --info-color: #4facfe;

  /* 文字颜色 */
  --text-primary: #303133;
  --text-regular: #606266;
  --text-secondary: #909399;
  --text-placeholder: #c0c4cc;

  /* 边框颜色 */
  --border-color: #dcdfe6;
  --border-light: #e4e7ed;
  --border-lighter: #ebeef5;
  --border-extra-light: #f2f6fc;

  /* 背景颜色 */
  --bg-color: #f5f7fa;
  --bg-white: #ffffff;
  --bg-overlay: rgba(0, 0, 0, 0.5);
  --bg-code: #f5f7fa;
  --bg-code-inline: #f0f0f5;
  --bg-blockquote: #f8f9ff;
  --bg-highlight-warm: #fff7ed;
  --bg-highlight-yellow: #fffbe6;
  --bg-table-alt: #f8f9fc;
  --bg-skeleton-start: #f0f2f5;
  --bg-skeleton-mid: #e6e8eb;
  --bg-table-header-from: #f8f9fc;
  --bg-table-header-to: #f4f6f9;
  --bg-table-striped: #fafbfc;

  /* 圆角 */
  --border-radius-small: 4px;
  --border-radius-base: 8px;
  --border-radius-large: 12px;
  --border-radius-circle: 50%;

  /* 阴影 */
  --box-shadow-light: 0 2px 12px rgba(0, 0, 0, 0.08);
  --box-shadow-base: 0 4px 16px rgba(0, 0, 0, 0.1);
  --box-shadow-dark: 0 8px 30px rgba(0, 0, 0, 0.15);

  /* Markdown/代码专用色 */
  --md-heading-border: #e8f0fe;
  --md-heading-text: #1a1a2e;
  --md-body-text: #3d3d3d;
  --md-code-accent: #e83e8c;
  --md-code-text: #555;
  --md-table-text: #555;
  --md-table-border: #eee;
  --md-blockquote-text: #556680;
  --md-strong-color: #e65100;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html,
body,
#app {
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB',
    'Microsoft YaHei', 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-size: 14px;
  color: var(--text-regular);
  background: var(--bg-color);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* 滚动条美化 */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: var(--bg-color);
  border-radius: 4px;
}

::-webkit-scrollbar-thumb {
  background: var(--border-color);
  border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

/* Element Plus 组件全局覆盖 */
.el-button {
  border-radius: var(--border-radius-base);
  font-weight: 500;
  transition: all 0.3s ease;
}

.el-button--primary {
  background: linear-gradient(135deg, var(--primary-color) 0%, var(--primary-light) 100%);
  border: none;
}

.el-button--primary:hover {
  transform: translateY(-2px);
  box-shadow: var(--box-shadow-base);
}

.el-card {
  border-radius: var(--border-radius-large);
  border: 1px solid var(--border-lighter);
  box-shadow: var(--box-shadow-light);
  transition: all 0.3s ease;
}

.el-card:hover {
  box-shadow: var(--box-shadow-base);
}

.el-input__wrapper {
  border-radius: var(--border-radius-base);
}

.el-select {
  --el-select-input-focus-border-color: var(--primary-color);
}

.el-tag {
  border-radius: var(--border-radius-small);
}

.el-dialog {
  border-radius: var(--border-radius-large);
  overflow: hidden;
}

.el-dialog__header {
  background: linear-gradient(135deg, var(--primary-color) 0%, var(--primary-light) 100%);
  padding: 16px 20px;
  margin: 0;
}

.el-dialog__title {
  color: var(--bg-white);
  font-weight: 600;
}

.el-dialog__headerbtn .el-dialog__close {
  color: var(--bg-white);
}

/* 表格样式 */
.el-table {
  border-radius: var(--border-radius-large);
  overflow: hidden;
}

.el-table th.el-table__cell {
  background: var(--bg-color);
  font-weight: 600;
}

/* 分页样式 */
.el-pagination {
  --el-pagination-button-bg-color: var(--bg-white);
  --el-pagination-hover-color: var(--primary-color);
}

.el-pagination.is-background .el-pager li.is-active {
  background: linear-gradient(135deg, var(--primary-color) 0%, var(--primary-light) 100%);
}

/* 菜单样式 */
.el-menu {
  border-right: none;
}

/* 动画过渡 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-enter-active,
.slide-leave-active {
  transition: all 0.3s ease;
}

.slide-enter-from {
  transform: translateX(-20px);
  opacity: 0;
}

.slide-leave-to {
  transform: translateX(20px);
  opacity: 0;
}

/* 选中文字样式 */
::selection {
  background: var(--primary-color);
  color: var(--bg-white);
}

/* ========== 毕业设计级别UI增强 ========== */

/* 输入框聚焦高亮 */
.el-input__wrapper:focus-within {
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.2) !important;
}

.el-input__wrapper.is-focus {
  border-color: var(--primary-color) !important;
}

.el-select .el-input.is-focus .el-input__wrapper {
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.2) !important;
}

/* 按钮hover效果 */
.el-button:not(.is-text):not(.is-link):hover {
  transform: translateY(-1px);
}

/* 卡片悬浮效果增强 */
.el-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.12) !important;
}

/* 表格增强 */
.el-table {
  --el-table-border-color: var(--border-lighter);
  --el-table-header-bg-color: var(--bg-white);
}

.el-table th.el-table__cell {
  background: linear-gradient(135deg, var(--bg-table-header-from) 0%, var(--bg-table-header-to) 100%) !important;
  color: var(--text-regular);
  font-weight: 600;
}

.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell {
  background: var(--bg-table-striped);
}

/* 对话框增强 */
.el-dialog {
  border-radius: 16px !important;
}

.el-dialog__header {
  border-radius: 16px 16px 0 0;
}

/* 消息提示优化 */
.el-message {
  border-radius: 8px !important;
  border: none !important;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.15) !important;
}

/* 下拉菜单优化 */
.el-dropdown-menu {
  border-radius: 12px !important;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.1) !important;
  border: none !important;
}

/* 标签优化 */
.el-tag {
  border-radius: 6px !important;
  font-weight: 500;
}

/* 分页器优化 */
.el-pagination .el-pager li:hover {
  color: var(--primary-color);
}

/* 加载动画 */
.el-loading-mask {
  background: rgba(255, 255, 255, 0.9);
}

.el-loading-spinner .circular {
  stroke: var(--primary-color);
}

/* 骨架屏优化 */
.el-skeleton__item {
  background: linear-gradient(90deg, var(--bg-skeleton-start) 25%, var(--bg-skeleton-mid) 50%, var(--bg-skeleton-start) 75%);
  background-size: 200% 100%;
  animation: skeleton-loading 1.5s infinite;
}

@keyframes skeleton-loading {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* 工具提示优化 */
.el-tooltip__popper {
  border-radius: 8px !important;
  font-size: 13px;
}

/* 进度条优化 */
.el-progress-bar__outer {
  border-radius: 10px;
}

.el-progress-bar__inner {
  border-radius: 10px;
}

/* 开关优化 */
.el-switch.is-checked .el-switch__core {
  background: linear-gradient(135deg, var(--primary-color) 0%, var(--primary-light) 100%);
  border-color: transparent;
}

/* 日期选择器优化 */
.el-date-editor.el-input__wrapper {
  border-radius: 8px;
}

/* 步骤条优化 */
.el-step__head.is-finish {
  color: var(--success-color);
  border-color: var(--success-color);
}

.el-step__title.is-finish {
  color: var(--success-color);
}

/* 折叠面板优化 */
.el-collapse {
  border: none;
}

.el-collapse-item__header {
  border-radius: 8px;
  margin-bottom: 8px;
}

.el-collapse-item__wrap {
  border: none;
}

/* 分割线优化 */
.el-divider {
  background: linear-gradient(90deg, transparent, var(--border-light), transparent);
}

.el-divider--horizontal {
  margin: 20px 0;
}

/* 徽标优化 */
.el-badge__content {
  border: none;
  box-shadow: 0 2px 8px rgba(245, 87, 108, 0.4);
}

/* 锚点导航优化 */
.el-anchor__link {
  border-radius: 6px;
}

.el-anchor__link.is-active {
  color: var(--primary-color);
}

/* 穿梭框优化 */
.el-transfer__buttons {
  padding: 0 8px;
}

/* 时间线优化 */
.el-timeline-item__node {
  background: linear-gradient(135deg, var(--primary-color) 0%, var(--primary-light) 100%);
}

/* 回到顶部 */
.el-backtop {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  border-radius: 50%;
}
</style>