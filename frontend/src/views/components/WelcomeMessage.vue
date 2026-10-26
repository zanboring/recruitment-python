<template>
  <div class="welcome-message">
    <div class="welcome-content">
      <el-icon class="welcome-icon"><ChatDotRound /></el-icon>
      <div class="welcome-text">
        <h3>欢迎使用AI智能分析助手！</h3>
        <p>我可以帮您分析招聘数据、薪资水平、行业趋势等信息。</p>
        <p>您可以：</p>
        <ul>
          <li>输入您的问题，如：分析长沙Java后端岗位的薪资水平</li>
          <li>点击下方的常用问题快速获取信息</li>
          <li>询问关于招聘、简历、面试的相关问题</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ChatDotRound } from '@element-plus/icons-vue';
</script>

<style scoped>
.welcome-message {
  display: flex;
  justify-content: center;
  margin-bottom: 30px;
}

.welcome-content {
  background: linear-gradient(135deg, #ffffff 0%, #f0f9eb 100%);
  border: 1px solid #e6f7ff;
  border-radius: 16px;
  padding: 24px;
  max-width: 600px;
  width: 100%;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
  display: flex;
  gap: 16px;
}

.welcome-icon {
  font-size: 48px;
  color: #409eff;
  flex-shrink: 0;
  margin-top: 8px;
}

.welcome-text {
  flex: 1;
}

.welcome-text h3 {
  color: #1a1a1a;
  margin: 0 0 12px 0;
  font-size: 18px;
}

.welcome-text p {
  color: #606266;
  margin: 8px 0;
  line-height: 1.5;
}

.welcome-text ul {
  padding-left: 20px;
  margin: 12px 0;
}

.welcome-text li {
  margin: 4px 0;
  color: #606266;
  line-height: 1.4;
}
</style>
