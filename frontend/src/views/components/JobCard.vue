<template>
  <div class="job-card-wrapper" @click="handleCardClick(job)">
  <el-card class="job-card" :class="cardClass" shadow="hover">
    <!-- 卡片头部：标题 + 状态标签 -->
    <div class="job-card-header">
      <div class="job-title-wrapper">
        <div class="job-title">{{ job.title }}</div>
        <el-tag :type="statusType" effect="dark" size="small" round>{{ statusText }}</el-tag>
      </div>
    </div>

    <!-- 公司信息 -->
    <div class="job-company">
      <el-icon><OfficeBuilding /></el-icon>
      <span class="company-name">{{ job.companyName || '公司信息待完善' }}</span>
    </div>

    <!-- 薪资信息 -->
    <div class="job-salary">
      <span class="salary-value">{{ formattedSalary }}</span>
      <span class="salary-unit">/月</span>
    </div>

    <!-- 详细信息行 -->
    <div class="job-info-row">
      <div class="info-item"><el-icon><Location /></el-icon><span>{{ job.city || '未知' }}</span></div>
      <div class="info-item"><el-icon><Reading /></el-icon><span>{{ job.education || '不限' }}</span></div>
      <div class="info-item"><el-icon><Clock /></el-icon><span>{{ job.experience || '经验不限' }}</span></div>
    </div>

    <!-- 岗位介绍（截断显示） -->
    <div class="job-desc" v-if="job.jobDesc">
      <div class="desc-title"><el-icon><Document /></el-icon>岗位介绍</div>
      <div class="desc-content">{{ truncatedDesc }}</div>
    </div>

    <!-- 技能标签 -->
    <div class="job-skills" v-if="skillList.length > 0">
      <el-tag v-for="(skill, index) in skillList.slice(0, 4)" :key="skill"
        :type="getSkillTagType(index)" effect="light" size="small" class="skill-tag">
        {{ skill }}
      </el-tag>
    </div>

    <!-- 底部信息栏：来源 + 操作按钮 + 发布时间 -->
    <div class="job-footer">
      <div class="footer-left">
        <el-tag :type="sourceType" size="small" effect="plain">{{ job.sourceSite || '未知' }}</el-tag>
        <!-- 【方案A增量】有真实URL时显示"查看原岗"按钮 -->
        <el-button v-if="hasOriginalUrl" type="primary" link size="small"
          @click.stop="openOriginalUrl" class="view-original-btn">
          <el-icon><Link /></el-icon>查看原岗
        </el-button>
        <!-- 【方案A增量】有本地详情时显示"查看本地详情"按钮 -->
        <el-button v-if="hasDetailHtml" type="success" link size="small"
          @click.stop="showLocalDetail" class="view-local-btn">
          <el-icon><Document /></el-icon>本地详情
        </el-button>
        <!-- 【方案A增量】下架岗位显示"查找类似岗位"按钮，引导用户搜索同类岗位 -->
        <el-button v-if="isOffline" type="warning" link size="small"
          @click.stop="$emit('click', job)" class="view-similar-btn">
          <el-icon><Search /></el-icon>查找类似岗位
        </el-button>
      </div>
      <span class="publish-time">{{ formattedDate }}</span>
    </div>
  </el-card>

  <!-- 【方案A增量】本地详情弹窗 -->
  <el-dialog
    v-model="localDetailVisible"
    title="本地岗位详情（原平台页面快照）"
    width="80%"
    :destroy-on-close="true"
  >
    <div v-if="localDetailHtml" v-html="sanitizedDetailHtml" class="local-detail-content"></div>
    <div v-else>加载中...</div>
  </el-dialog>
</div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { OfficeBuilding, Location, Reading, Clock, Document, Link, Search } from '@element-plus/icons-vue';
import { isPreciseJobSourceUrl } from '@/utils/jobSourceUrl';
import { getJobDetailHtml } from '@/api/job';

interface JobItem {
  id: number; title: string; companyName?: string; sourceSite: string;
  jobStatus: string; city: string; experience: string; education: string;
  minSalary?: number | string; maxSalary?: number | string; salaryUnit: string;
  skills: string; jobDesc?: string; url?: string; publishTime?: string;
  detailHtml?: string; // 新增：本地存储的详情页HTML
}

const props = defineProps<{ job: JobItem }>();
const emit = defineEmits<{ click: [job: JobItem] }>();

// ===== 方案A：本地详情弹窗控制 =====
const localDetailVisible = ref(false);
const localDetailHtml = ref('');

/** 是否有本地存储的详情HTML */
const hasDetailHtml = computed(() => {
  return props.job.detailHtml != null && props.job.detailHtml.trim().length > 0;
});

/** 净化HTML内容，防止XSS */
const sanitizeHtml = (html: string) => {
  if (!html) return '';
  // 简单净化：移除script标签和on事件属性
  return html
    .replace(/<script[^>]*>.*?<\/script>/gis, '')
    .replace(/\s+on\w+\s*=\s*["'][^"']*["']/gi, '')
    .replace(/\s+on\w+\s*=\s*[^"'\s>]+/gi, '');
};

const sanitizedDetailHtml = computed(() => sanitizeHtml(localDetailHtml.value));

// ===== 计算属性 =====

const statusType = computed(() => {
  const map: Record<string, string> = { NEW: 'warning', ACTIVE: 'success', OFFLINE: 'info' };
  return map[props.job.jobStatus] || 'info';
});

const statusText = computed(() => {
  const map: Record<string, string> = { NEW: '新增', ACTIVE: '在岗', OFFLINE: '已下架' };
  return map[props.job.jobStatus] || props.job.jobStatus;
});

const sourceType = computed(() => {
  const map: Record<string, string> = {
    'BOSS直聘': 'danger', 'BOSS': 'danger', '智联招聘': 'primary', '前程无忧': 'success', '猎聘': 'warning'
  };
  return map[props.job.sourceSite] || 'info';
});

const skillList = computed(() => (props.job.skills || '').split(',').filter(s => s.trim()));

const hasOriginalUrl = computed(() => isPreciseJobSourceUrl(props.job.url));

/** 【方案A增量】判断岗位是否已下架，用于显示"查找类似岗位"按钮 */
const isOffline = computed(() => props.job.jobStatus === 'OFFLINE');

/** 【方案A增量】下架岗位卡片整体灰化处理，通过CSS类实现 */
const cardClass = computed(() => ({
  'job-card--offline': isOffline.value
}));

/** 格式化薪资显示 */
const formattedSalary = computed(() => {
  const min = Number(props.job.minSalary) || 0;
  const max = Number(props.job.maxSalary) || 0;
  if (!min && !max) return '面议';
  if (min && max) return `${min}-${max}`;
  if (min) return `${min}+`;
  if (max) return `${max}以下`;
  return '面议';
});

/** 截断描述文本 */
const truncatedDesc = computed(() => {
  if (!props.job.jobDesc) return '';
  const desc = String(props.job.jobDesc);
  return desc.length > 100 ? desc.substring(0, 100) + '...' : desc;
});

/** 格式化发布日期 */
const formattedDate = computed(() => {
  if (!props.job.publishTime) return '未知';
  const time = props.job.publishTime;
  // 支持 Date 对象或字符串
  if (Object.prototype.toString.call(time) === '[object Date]') {
    return (time as unknown as Date).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
  }
  if (typeof time === 'object' && time !== null && 'toISOString' in time) {
    return String(time).substring(0, 10);
  }
  if (typeof time === 'string') {
    return time.length >= 10 ? time.substring(0, 10) : time;
  }
  return String(time).substring(0, 10);
});

/** 根据索引返回技能标签颜色类型 */
const getSkillTagType = (index: number | string) => {
  const types = ['primary', 'success', 'warning', 'danger'];
  return types[Number(index) % 4];
};

// ===== 方案A：跳转 + 本地详情逻辑 =====

/** 优先跳转原平台URL */
const openOriginalUrl = () => {
  if (props.job.url) {
    window.open(props.job.url, '_blank');
  }
};

/** 卡片点击事件：触发父组件的点击处理 */
const handleCardClick = (job: JobItem) => {
  emit('click', job);
};

/** 显示本地存储的详情页HTML */
const showLocalDetail = async () => {
  // 如果本地已有缓存，直接显示
  if (localDetailHtml.value) {
    localDetailVisible.value = true;
    return;
  }
  // 否则从后端获取详情HTML
  try {
    const res = await getJobDetailHtml(props.job.id);
    localDetailHtml.value = res.data?.detailHtml || '';
    localDetailVisible.value = true;
  } catch (err) {
    console.error('获取本地详情失败：', err);
    // 如果后端没有存储，则尝试直接渲染job对象中的detailHtml字段
    localDetailHtml.value = props.job.detailHtml || '';
    localDetailVisible.value = true;
  }
};

</script>

<style scoped>
.job-card-wrapper {
  cursor: pointer;
  height: 100%;
}
.job-card {
  height: 100%; border: none; border-radius: 16px; background: #fff;
  transition: all .4s cubic-bezier(.4, 0, .2, 1); overflow: hidden;
}
.job-card-wrapper:hover .job-card { transform: translateY(-8px); box-shadow: 0 16px 40px rgba(64,158,255,.15) !important; }

.job-card :deep(.el-card__body) { padding: 20px; }

.job-card-header { margin-bottom: 12px; }
.job-title-wrapper {
  display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;
}
.job-title {
  font-size: 16px; font-weight: 700; color: var(--text-primary); line-height: 1.4;
  flex: 1; overflow: hidden; text-overflow: ellipsis;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}

/* 公司 */
.job-company { display: flex; align-items: center; gap: 6px; font-size: 14px; color: var(--text-secondary); margin-bottom: 12px; }
.company-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 薪资 */
.job-salary { margin-bottom: 14px; }
.salary-value {
  font-size: 24px; font-weight: 800;
  background: linear-gradient(135deg, #f56c6c, #e6a23c);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.salary-unit { font-size: 12px; color: var(--text-placeholder); margin-left: 4px; }

/* 信息行 */
.job-info-row { display: flex; gap: 16px; margin-bottom: 14px; flex-wrap: wrap; }
.info-item {
  display: flex; align-items: center; gap: 4px; font-size: 13px;
  color: var(--text-regular); background: var(--bg-color); padding: 4px 10px; border-radius: 6px;
}
.info-item .el-icon { color: var(--text-secondary); }

/* 描述 */
.job-desc { margin-bottom: 14px; padding: 12px; background: #f8f9fa; border-radius: 8px; }
.desc-title { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; color: var(--text-regular); margin-bottom: 8px; }
.desc-content { font-size: 12px; line-height: 1.4; color: var(--text-regular); overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; }

/* 技能标签 */
.job-skills { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
.skill-tag { font-size: 12px; border-radius: 4px; }

/* 底部 */
.job-footer { display: flex; justify-content: space-between; align-items: center; padding-top: 14px; border-top: 1px dashed var(--border-lighter); }
.footer-left { display: flex; align-items: center; gap: 8px; }
.view-original-btn { font-size: 12px; font-weight: 500; }
/* 【方案A增量】类似岗位按钮样式 */
.view-similar-btn { font-size: 12px; font-weight: 500; color: #e6a23c; }
.view-similar-btn:hover { color: #f56c6c; }
.publish-time { font-size: 12px; color: var(--text-placeholder); }

/* 【方案A增量】下架岗位卡片整体灰化降权视觉效果 */
.job-card--offline {
  opacity: 0.72;
  filter: grayscale(30%);
}
.job-card--offline .job-title {
  color: var(--text-secondary) !important;
}
.job-card--offline:hover {
  opacity: 0.92;
  filter: grayscale(10%);
}
</style>
