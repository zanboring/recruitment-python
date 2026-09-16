<template>
  <el-row :gutter="20" style="margin-top: 20px">
    <el-col :span="24">
      <el-card class="preview-card" shadow="never">
        <template #header>
          <div class="card-header">
            <div class="title">
              <el-icon><View /></el-icon>
              <span>数据预览</span>
            </div>
            <div class="preview-actions">
              <el-input
                v-model="previewQuery.keyword"
                placeholder="搜索岗位"
                clearable
                style="width: 200px; margin-right: 10px"
                @keyup.enter="loadPreviewData"
              />
              <el-button type="primary" size="small" @click="loadPreviewData">
                <el-icon><Search /></el-icon>
                搜索
              </el-button>
            </div>
          </div>
        </template>

        <el-table :data="previewData" v-loading="previewLoading" stripe max-height="400">
          <el-table-column prop="title" label="岗位名称" width="200" show-overflow-tooltip />
          <el-table-column prop="companyName" label="公司名称" width="180" show-overflow-tooltip />
          <el-table-column prop="city" label="城市" width="100" />
          <el-table-column prop="experience" label="经验" width="100" />
          <el-table-column prop="education" label="学历" width="100" />
          <el-table-column label="薪资" width="150">
            <template #default="{ row }">
              {{ formatSalary(row.minSalary, row.maxSalary) }}
            </template>
          </el-table-column>
          <el-table-column prop="sourceSite" label="来源" width="100" />
          <el-table-column prop="createdAt" label="创建时间" width="180" />
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.url && row.url !== '' && !row.url.includes('baidu.com')" type="primary" link size="small" @click="openJobUrl(row.url)">
                <el-icon><Link /></el-icon>
                查看原岗
              </el-button>
              <span v-else class="no-link-text">暂无链接</span>
            </template>
          </el-table-column>
        </el-table>

        <el-empty v-if="previewData.length === 0 && !previewLoading" description="暂无数据" />
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { reactive } from 'vue';
import { View, Search, Link } from '@element-plus/icons-vue';
import http from '@/api/http';

interface Job {
  id?: number;
  title: string;
  companyName: string;
  city: string;
  experience: string;
  education: string;
  minSalary?: number;
  maxSalary?: number;
  sourceSite: string;
  createdAt: string;
  url?: string;
}

// Props
defineProps<{
  previewData: Job[];
  previewLoading: boolean;
}>();

const emit = defineEmits<{
  (e: 'loadPreviewData', query: { keyword: string }): void;
}>();

const previewQuery = reactive({
  keyword: ''
});

const loadPreviewData = () => {
  emit('loadPreviewData', previewQuery);
};

const openJobUrl = (url: string) => {
  window.open(url, '_blank');
};

const formatSalary = (min?: number, max?: number) => {
  if (!min && !max) return '面议';
  const minK = min ? (min / 1000).toFixed(1) : '';
  const maxK = max ? (max / 1000).toFixed(1) : '';
  if (min && max) return `${minK}-${maxK}K`;
  if (min) return `${minK}K+`;
  if (max) return `${maxK}K以下`;
  return '面议';
};
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header .title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #333;
}

.preview-actions {
  display: flex;
  align-items: center;
}

.no-link-text {
  color: #c0c4cc;
  font-size: 12px;
}
</style>
