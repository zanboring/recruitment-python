<template>
  <el-dialog v-model="dialogVisible" title="AI智能分析报告" width="85%" top="3vh"
    class="ai-dialog" :close-on-click-modal="false">
    <div v-loading="loading" class="ai-analysis-content">
      <!-- 分析摘要 -->
      <el-alert v-if="result.summary" :title="result.summary" type="success" :closable="false" show-icon class="analysis-summary" />

      <!-- 优质岗位推荐 -->
      <div class="analysis-section">
        <div class="section-header"><el-icon><Star /></el-icon>优质岗位推荐</div>
        <el-table :data="result.qualityJobs" stripe class="analysis-table">
          <el-table-column prop="title" label="岗位名称" width="180" show-overflow-tooltip />
          <el-table-column prop="companyName" label="公司" width="150" show-overflow-tooltip />
          <el-table-column prop="city" label="城市" width="100" />
          <el-table-column prop="salary" label="薪资" width="140" />
          <el-table-column prop="skills" label="技能要求" show-overflow-tooltip />
          <el-table-column prop="recommendReason" label="推荐理由" show-overflow-tooltip />
        </el-table>
      </div>

      <!-- 需求趋势分析 -->
      <div class="analysis-section" v-if="result.trendAnalysis">
        <div class="section-header"><el-icon><TrendCharts /></el-icon>需求趋势分析</div>
        <el-descriptions :column="3" border class="trend-desc">
          <el-descriptions-item label="热门城市">
            <el-tag type="primary">{{ result.trendAnalysis.hotCity }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="热门技能">
            <el-tag type="success">{{ result.trendAnalysis.hotSkill }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="热门岗位">
            <el-tag type="warning">{{ result.trendAnalysis.hotTitle }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>
      </div>

      <!-- 技能需求排名 -->
      <div class="analysis-section" v-if="result.skillDemands && result.skillDemands.length > 0">
        <div class="section-header"><el-icon><Histogram /></el-icon>技能需求排名</div>
        <el-row :gutter="12">
          <el-col :span="6" v-for="(skill, index) in result.skillDemands" :key="skill.skill">
            <el-card shadow="hover" class="skill-card" :class="'skill-rank-' + (Number(index)+1)">
              <div class="skill-rank">{{ Number(index) + 1 }}</div>
              <div class="skill-name">{{ skill.skill }}</div>
              <div class="skill-count">{{ skill.count }}个岗位</div>
              <el-tag :type="getSkillLevelType(skill.level)" size="small">{{ skill.level }}</el-tag>
            </el-card>
          </el-col>
        </el-row>
      </div>

      <!-- 薪资分析 -->
      <div class="analysis-section" v-if="result.salaryAnalysis">
        <div class="section-header"><el-icon><Money /></el-icon>薪资分析</div>
        <el-descriptions :column="3" border class="salary-desc">
          <el-descriptions-item label="平均薪资"><span class="salary-highlight">{{ result.salaryAnalysis.avgSalary }}</span></el-descriptions-item>
          <el-descriptions-item label="最高薪资"><span class="salary-highlight">{{ result.salaryAnalysis.topSalary }}</span></el-descriptions-item>
          <el-descriptions-item label="薪资范围"><span class="salary-highlight">{{ result.salaryAnalysis.salaryRange }}</span></el-descriptions-item>
        </el-descriptions>
      </div>

      <!-- 求职建议 -->
      <div class="analysis-section" v-if="result.suggestions && result.suggestions.length > 0">
        <div class="section-header"><el-icon><ChatDotRound /></el-icon>求职建议</div>
        <el-card class="suggestions-card">
          <el-timeline>
            <el-timeline-item v-for="(suggestion, index) in result.suggestions" :key="index"
              :timestamp="suggestion" placement-top :icon="Calendar" />
          </el-timeline>
        </el-card>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { Star, TrendCharts, Histogram, Money, ChatDotRound, Calendar } from '@element-plus/icons-vue';

interface AISkillDemand { skill: string; count: number; level: string; }
interface AIResult {
  summary: string; qualityJobs: any[]; trendAnalysis?: { hotCity: string; hotSkill: string; hotTitle: string };
  skillDemands: AISkillDemand[]; salaryAnalysis?: { avgSalary: string; topSalary: string; salaryRange: string };
  suggestions: string[];
}

const props = defineProps<{ visible: boolean; loading: boolean; result: AIResult }>();
const emit = defineEmits<{ 'update:visible': [value: boolean] }>();

const dialogVisible = computed({
  get: () => props.visible,
  set: (val) => emit('update:visible', val)
});

const getSkillLevelType = (level: string) => {
  const map: Record<string, string> = { '非常热门': 'danger', '热门': 'warning', '一般': 'info' };
  return map[level] || 'info';
};
</script>

<style scoped>
.ai-analysis-content { max-height: 75vh; overflow-y: auto; padding: 8px; }

.analysis-summary { margin-bottom: 20px; border-radius: 12px; }
.analysis-section { margin-bottom: 24px; }
.section-header {
  display: flex; align-items: center; gap: 8px;
  font-size: 16px; font-weight: 600; color: var(--text-primary);
  margin-bottom: 16px; padding-left: 12px; border-left: 4px solid var(--info-color);
}
.section-header .el-icon { font-size: 18px; color: var(--info-color); }

/* 技能排名卡片 */
.skill-card {
  text-align: center; padding: 16px; border-radius: 12px;
  transition: all .3s ease; position: relative; overflow: hidden;
}
.skill-card:hover { transform: translateY(-4px); }
.skill-rank {
  position: absolute; top: 8px; right: 8px; width: 24px; height: 24px;
  background: linear-gradient(135deg, #667eea, #764ba2); color: #fff;
  border-radius: 50%; font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}
.skill-rank-1 { background: linear-gradient(135deg, #fa709a, #fee140); }
.skill-rank-2 { background: linear-gradient(135deg, #f093fb, #f5576c); }
.skill-rank-3 { background: linear-gradient(135deg, #4facfe, #00f2fe); }
.skill-name { font-size: 15px; font-weight: 600; color: var(--text-primary); margin: 8px 0 4px; }
.skill-count { font-size: 13px; color: var(--text-secondary); margin-bottom: 8px; }

.salary-highlight { font-size: 15px; font-weight: 600; color: #f56c6c; }

.suggestions-card { border-radius: 12px; }
.suggestions-card :deep(.el-card__body) { padding: 20px; }
</style>
