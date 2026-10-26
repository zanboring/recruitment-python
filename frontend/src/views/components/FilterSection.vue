<template>
  <div class="filter-section">
    <el-card class="filter-card" shadow="never">
      <!-- 第一行：关键词 + 城市 + 来源 + 学历 + 经验 -->
      <el-row :gutter="20" align="middle" class="filter-row">
        <el-col :span="5">
          <el-input v-model="localQuery.keyword" placeholder="岗位名称/公司/技能"
            clearable @keyup.enter="$emit('search')" size="large" style="width: 220px">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </el-col>
        <el-col :span="4">
          <el-select v-model="localQuery.city" placeholder="选择城市" clearable filterable size="large" style="width: 220px">
            <el-option label="北京" value="北京" /><el-option label="上海" value="上海" />
            <el-option label="广州" value="广州" /><el-option label="深圳" value="深圳" />
            <el-option label="杭州" value="杭州" /><el-option label="南京" value="南京" />
            <el-option label="成都" value="成都" /><el-option label="武汉" value="武汉" />
            <el-option label="西安" value="西安" /><el-option label="苏州" value="苏州" />
            <el-option label="重庆" value="重庆" /><el-option label="天津" value="天津" />
            <el-option label="长沙" value="长沙" /><el-option label="东莞" value="东莞" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-select v-model="localQuery.sourceSite" placeholder="来源网站" clearable size="large" style="width: 220px">
            <el-option label="BOSS直聘" value="BOSS直聘" /><el-option label="智联招聘" value="智联招聘" />
            <el-option label="前程无忧" value="前程无忧" /><el-option label="猎聘" value="猎聘" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-select v-model="localQuery.education" placeholder="学历" clearable size="large" style="width: 220px">
            <el-option label="不限" value="不限" /><el-option label="大专" value="大专" />
            <el-option label="本科" value="本科" /><el-option label="硕士" value="硕士" />
            <el-option label="博士" value="博士" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-select v-model="localQuery.experience" placeholder="经验" clearable size="large" style="width: 220px">
            <el-option label="不限" value="经验不限" /><el-option label="应届" value="应届" />
            <el-option label="1-3年" value="1-3年" /><el-option label="3-5年" value="3-5年" />
            <el-option label="5-10年" value="5-10年" />
          </el-select>
        </el-col>
      </el-row>

      <!-- 第二行：薪资范围 + 操作按钮 -->
      <el-row :gutter="20" align="middle" class="filter-row">
        <el-col :span="6">
          <div class="salary-range">
            <el-input-number v-model="localQuery.minSalary" :min="0" :step="1000"
              placeholder="最低薪资" controls-position="right" size="large" style="width: 120px" />
            <span class="salary-separator">-</span>
            <el-input-number v-model="localQuery.maxSalary" :min="0" :step="1000"
              placeholder="最高薪资" controls-position="right" size="large" style="width: 120px" />
          </div>
        </el-col>
        <el-col :span="18">
          <el-space>
            <el-button type="primary" size="large" @click="$emit('search')" class="action-btn">
              <el-icon><Search /></el-icon>搜索
            </el-button>
            <el-button size="large" @click="$emit('reset')">重置</el-button>
            <el-button type="success" size="large" @click="$emit('ai-analysis')" class="action-btn">
              <el-icon><DataAnalysis /></el-icon>AI分析
            </el-button>
            <el-button type="warning" size="large" @click="$emit('salary-predict')" class="action-btn">
              <el-icon><Promotion /></el-icon>薪资预测
            </el-button>
            <el-button type="info" size="large" @click="$emit('recommend')" class="action-btn">
              <el-icon><Star /></el-icon>智能推荐
            </el-button>
            <el-button type="primary" size="large" @click="$emit('quick-crawl')">
              <el-icon><Connection /></el-icon>一键爬取
            </el-button>
            <el-button type="warning" size="large" @click="$emit('export-excel')" class="action-btn">
              <el-icon><Download /></el-icon>导出Excel
            </el-button>
          </el-space>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, watch } from 'vue';
import { Search, DataAnalysis, Promotion, Star, Connection, Download } from '@element-plus/icons-vue';

interface JobQuery {
  keyword?: string; city?: string; sourceSite?: string; education?: string;
  experience?: string; minSalary?: number | undefined; maxSalary?: number | undefined;
  status?: string; pageNum?: number; pageSize?: number;
}

const props = defineProps<{ query: JobQuery }>();
const emit = defineEmits<{
  'update:query': [value: JobQuery];
  search: []; reset: [];
  'ai-analysis': []; 'salary-predict': []; recommend: [];
  'quick-crawl': []; 'export-excel': [];
}>();

const localQuery = reactive<JobQuery>({ ...props.query });

watch(() => props.query, (val) => Object.assign(localQuery, val), { deep: true });

watch(localQuery, (val) => emit('update:query', val), { deep: true });
</script>

<style scoped>
.filter-section { padding: 4px 0; }
.filter-row { margin-bottom: 20px; }
.filter-row:last-child { margin-bottom: 0; }

.salary-range {
  display: flex; align-items: center;
}
.salary-separator {
  margin: 0 8px; color: var(--text-secondary); font-weight: 600;
}
.action-btn { font-weight: 600; }

/* 筛选卡片样式 */
.filter-card { border: none; border-radius: 16px; background: #fff; margin-bottom: 20px; }
.filter-card :deep(.el-card__body) { padding: 20px; }
</style>
