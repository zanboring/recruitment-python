<template>
  <el-dialog v-model="dialogVisible" title="⭐ 岗位智能推荐" width="85%" top="3vh">
    <div v-loading="loading">
      <!-- 提示信息 -->
      <el-alert type="info" show-icon :closable="false" style="margin-bottom:16px">
        基于您的技能标签、学历、工作经验和目标城市，从当前数据中智能匹配最适合的岗位
      </el-alert>

      <!-- 筛选条件行 -->
      <el-row :gutter="16" align="middle" style="margin-bottom:16px">
        <el-col :span="6">
          <el-input v-model="skillsInput" placeholder="输入技能关键词，如：Java,Vue,Python"
            clearable @keyup.enter="doSearch" size="large">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </el-col>
        <el-col :span="4">
          <el-select v-model="educationSelect" placeholder="学历" clearable size="large" style="width:100%">
            <el-option label="不限" value="不限" />
            <el-option label="大专" value="大专" />
            <el-option label="本科" value="本科" />
            <el-option label="硕士" value="硕士" />
            <el-option label="博士" value="博士" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-input-number v-model="experienceInput" placeholder="工作年限" 
            :min="0" :max="40" size="large" style="width:100%" />
        </el-col>
        <el-col :span="4">
          <el-select v-model="citySelect" placeholder="目标城市" clearable size="large" style="width:100%">
            <el-option label="北京" value="北京" /><el-option label="上海" value="上海" />
            <el-option label="广州" value="广州" /><el-option label="深圳" value="深圳" />
            <el-option label="杭州" value="杭州" /><el-option label="长沙" value="长沙" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-button type="primary" size="large" @click="doSearch" :loading="loading" style="width:100%">开始匹配</el-button>
        </el-col>
        <el-col :span="2" style="text-align:right">
          <span class="recommend-count">共推荐 {{ recommendList.length }} 个岗位</span>
        </el-col>
      </el-row>

      <!-- 推荐结果表格 -->
      <el-table :data="recommendList" stripe v-if="recommendList.length > 0" style="width:100%">
        <el-table-column prop="title" label="岗位名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="companyName" label="公司" min-width="180" show-overflow-tooltip />
        <el-table-column prop="city" label="城市" width="90" />
        <el-table-column label="薪资" width="140">
          <template #default="{ row }">
            <span class="salary-text">{{ formatSalary(row.minSalary, row.maxSalary) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="skills" label="技能要求" min-width="200" show-overflow-tooltip />
        <el-table-column prop="education" label="学历" width="80" />
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="handleViewDetail(row)">查看详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无推荐结果，请调整技能或城市条件后重试" />
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { Search } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { isPreciseJobSourceUrl } from '@/utils/jobSourceUrl';

interface RecommendItem {
  id: number; title: string; companyName: string; city: string;
  minSalary?: number | string; maxSalary?: number | string;
  skills: string; education: string;
  url?: string; sourceSite?: string;
}

const props = defineProps<{
  visible: boolean; loading: boolean; recommendList: RecommendItem[]; initCity?: string;
}>();

const emit = defineEmits<{
  'update:visible': [value: boolean];
  search: [{ skills?: string; education?: string; experience?: number; city?: string }]
}>();

const dialogVisible = computed({
  get: () => props.visible,
  set: (val) => emit('update:visible', val)
});

const skillsInput = ref('');
const educationSelect = ref('');
const experienceInput = ref<number | undefined>(undefined);
const citySelect = ref('');

// 每次打开弹窗时重置搜索条件
watch(() => props.visible, (val) => { 
  if (val) { 
    skillsInput.value = ''; 
    educationSelect.value = '';
    experienceInput.value = undefined;
    citySelect.value = props.initCity || ''; 
  } 
});

const doSearch = () => {
  emit('search', { 
    skills: skillsInput.value || undefined, 
    education: educationSelect.value || undefined,
    experience: experienceInput.value,
    city: citySelect.value || undefined 
  });
};

const formatSalary = (min?: number | string, max?: number | string): string => {
  const minNum = Number(min) || 0; const maxNum = Number(max) || 0;
  if (!minNum && !maxNum) return '面议';
  if (minNum && maxNum) return `${minNum}-${maxNum}`;
  if (minNum) return `${minNum}+`;
  if (maxNum) return `${maxNum}以下`; return '面议';
};

const handleViewDetail = (job: RecommendItem) => {
  if (isPreciseJobSourceUrl(job.url)) {
    window.open(job.url as string, '_blank', 'noopener,noreferrer');
    return;
  }
  ElMessage.warning('该推荐结果未包含招聘网站详情页链接，无法直达原文。');
};
</script>

<style scoped>
.recommend-count { color: var(--text-secondary); font-size: 13px; }
.salary-text { color: #e6a23c; font-weight: 600; }
</style>
