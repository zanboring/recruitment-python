<template>
  <div class="skill-profile-page">
    <!-- 顶部：技能输入 -->
    <el-card shadow="hover" class="input-card">
      <div class="input-header">
        <h3 class="card-title">
          <el-icon><UserFilled /></el-icon>&nbsp;技能画像匹配
        </h3>
        <span class="hint">输入你掌握的技能，系统逐岗位计算覆盖率，找出你适合的岗位与技能缺口</span>
      </div>
      <div class="input-row">
        <el-input
          v-model="skillsInput"
          placeholder="例如：Python, FastAPI, RAG, Docker, Vue（逗号分隔）"
          class="skills-input"
          size="large"
          clearable
        />
        <el-select v-model="cityFilter" placeholder="全部城市" clearable size="large" class="city-select">
          <el-option v-for="c in cities" :key="c" :label="c" :value="c" />
        </el-select>
        <el-button type="primary" size="large" :loading="loading" @click="runProfile">
          <el-icon><Search /></el-icon>&nbsp;分析我的匹配
        </el-button>
      </div>
    </el-card>

    <!-- 结果区域 -->
    <template v-if="result">
      <!-- 概览 -->
      <el-row :gutter="16" class="overview-row">
        <el-col :span="8">
          <div class="ov-card">
            <div class="ov-num">{{ result.summary.analyzed }}</div>
            <div class="ov-label">已分析岗位数</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="ov-card">
            <div class="ov-num">{{ Math.round(result.summary.covered_ratio * 100) }}%</div>
            <div class="ov-label">我的平均技能覆盖率</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="ov-card">
            <div class="ov-num" style="color:#e6a23c">{{ result.my_skills.length }}</div>
            <div class="ov-label">我输入的技能数</div>
          </div>
        </el-col>
      </el-row>

      <!-- 两列：缺口技能 + 市场热门 -->
      <el-row :gutter="16" class="charts-row">
        <el-col :span="12">
          <el-card shadow="never" class="chart-card">
            <template #header>
              <span class="card-title"><el-icon color="#f56c6c"><Warning /></el-icon>&nbsp;我的技能缺口（岗位要我缺什么）</span>
            </template>
            <div v-if="result.missing_skills.length" class="tag-cloud">
              <el-tag
                v-for="(s, i) in result.missing_skills"
                :key="s.skill"
                :size="i < 5 ? 'large' : 'default'"
                :type="i < 3 ? 'danger' : i < 8 ? 'warning' : 'info'"
                effect="light"
                class="gap-tag"
              >{{ s.skill }} ×{{ s.count }}</el-tag>
            </div>
            <el-empty v-else description="恭喜，暂无技能缺口" />
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never" class="chart-card">
            <template #header>
              <span class="card-title"><el-icon color="#67c23a"><TrendCharts /></el-icon>&nbsp;市场 Top 需求技能</span>
            </template>
            <div v-if="result.top_demanded.length" class="tag-cloud">
              <el-tag
                v-for="(s, i) in result.top_demanded.slice(0, 12)"
                :key="s.skill"
                :size="i < 5 ? 'large' : 'default'"
                :type="i < 3 ? 'success' : 'primary'"
                effect="plain"
                class="demand-tag"
              >{{ s.skill }} ×{{ s.count }}</el-tag>
            </div>
            <el-empty v-else description="暂无数据" />
          </el-card>
        </el-col>
      </el-row>

      <!-- 匹配岗位列表 -->
      <el-card shadow="never" class="jobs-card">
        <template #header>
          <span class="card-title"><el-icon color="#409eff"><Briefcase /></el-icon>&nbsp;匹配岗位（按技能覆盖率排序）</span>
        </template>
        <el-table :data="result.jobs" stripe>
          <el-table-column prop="title" label="岗位" min-width="180" />
          <el-table-column prop="company_name" label="公司" min-width="140" />
          <el-table-column prop="city" label="城市" width="80" />
          <el-table-column label="薪资" width="130">
            <template #default="{ row }">
              <span v-if="row.min_salary">{{ Math.round(row.min_salary / 1000) }}K-{{ Math.round(row.max_salary / 1000) }}K</span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column label="覆盖率" width="150">
            <template #default="{ row }">
              <el-progress :percentage="Math.round(row.coverage * 100)" :stroke-width="10" :color="coverageColor(row.coverage)" />
            </template>
          </el-table-column>
          <el-table-column label="我已掌握" min-width="140">
            <template #default="{ row }">
              <el-tag v-for="m in row.matched" :key="m" size="small" type="success" effect="light" class="mini-tag">{{ m }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="我缺的" min-width="140">
            <template #default="{ row }">
              <el-tag v-for="m in row.missing_top" :key="m" size="small" type="info" effect="plain" class="mini-tag">{{ m }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <!-- 初始引导 -->
    <el-empty v-else-if="!loading" description="输入你的技能，点击「分析我的匹配」查看" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import { UserFilled, Search, Warning, TrendCharts, Briefcase } from '@element-plus/icons-vue';
import { skillProfile, type SkillProfileResult } from '@/api/job';

const skillsInput = ref('');
const cityFilter = ref('');
const loading = ref(false);
const result = ref<SkillProfileResult | null>(null);

const cities = ['北京', '上海', '深圳', '杭州', '广州', '成都', '武汉', '长沙', '南京', '西安'];

const coverageColor = (c: number) => (c >= 0.6 ? '#67c23a' : c >= 0.4 ? '#e6a23c' : '#f56c6c');

const runProfile = async () => {
  if (!skillsInput.value.trim()) {
    ElMessage.warning('请先输入你掌握的技能');
    return;
  }
  loading.value = true;
  try {
    result.value = await skillProfile({
      skills: skillsInput.value,
      city: cityFilter.value || undefined,
      limit: 20
    });
  } catch (e: any) {
    ElMessage.error(e?.message || '分析失败');
  } finally {
    loading.value = false;
  }
};
</script>

<style scoped>
.skill-profile-page { max-width: 1400px; margin: 0 auto; padding: 20px; }
.input-card { margin-bottom: 16px; }
.input-header { margin-bottom: 14px; }
.card-title { font-weight: 600; font-size: 15px; display: flex; align-items: center; }
.hint { font-size: 12px; color: #909399; margin-left: 30px; }
.input-row { display: flex; gap: 12px; }
.skills-input { flex: 1; }
.city-select { width: 150px; }
.overview-row { margin-bottom: 16px; }
.ov-card { text-align: center; padding: 22px 0; background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.06); }
.ov-num { font-size: 30px; font-weight: 700; color: #409eff; }
.ov-label { color: #909399; font-size: 13px; margin-top: 4px; }
.charts-row { margin-bottom: 16px; }
.chart-card :deep(.el-card__header) { padding: 12px 16px; }
.tag-cloud { display: flex; flex-wrap: wrap; gap: 10px; padding: 6px 0; }
.gap-tag, .demand-tag { border-radius: 16px; }
.jobs-card :deep(.el-card__header) { padding: 12px 16px; }
.mini-tag { margin: 2px 4px 2px 0; border-radius: 10px; }
</style>