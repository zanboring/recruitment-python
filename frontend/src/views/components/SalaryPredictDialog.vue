<template>
  <el-dialog v-model="dialogVisible" title="🎯 智能薪资预测" width="520px" class="salary-predict-dialog">
    <div v-loading="loading">
      <el-form :model="form" label-width="90px" size="large">
        <el-form-item label="目标城市">
          <el-select v-model="form.city" placeholder="选择城市" clearable filterable style="width:100%">
            <el-option label="北京" value="北京" /><el-option label="上海" value="上海" />
            <el-option label="深圳" value="深圳" /><el-option label="杭州" value="杭州" />
            <el-option label="广州" value="广州" /><el-option label="南京" value="南京" />
            <el-option label="成都" value="成都" /><el-option label="武汉" value="武汉" />
            <el-option label="西安" value="西安" /><el-option label="长沙" value="长沙" />
          </el-select>
        </el-form-item>
        <el-form-item label="工作经验">
          <el-select v-model="form.experience" placeholder="选择经验" style="width:100%">
            <el-option label="应届生" value="应届" /><el-option label="经验不限" value="经验不限" />
            <el-option label="1-3年" value="1-3年" /><el-option label="3-5年" value="3-5年" />
            <el-option label="5-10年" value="5-10年" /><el-option label="10年以上" value="10年以上" />
          </el-select>
        </el-form-item>
        <el-form-item label="学历要求">
          <el-select v-model="form.education" placeholder="选择学历" style="width:100%">
            <el-option label="大专" value="大专" /><el-option label="本科" value="本科" />
            <el-option label="硕士" value="硕士" /><el-option label="博士" value="博士" />
          </el-select>
        </el-form-item>
        <el-form-item label="技能关键词">
          <el-input v-model="form.skills" placeholder="如：Java,Python,Vue（逗号分隔）" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handlePredict" :loading="loading" style="width:100%" size="large">
            开始预测
          </el-button>
        </el-form-item>
      </el-form>

      <!-- 预测结果 -->
      <div v-if="predictedSalary !== null && predictedSalary !== undefined" class="predict-result">
        <el-result icon="success" title="预测完成"
          :sub-title="'基于 ' + (totalJobs || 0) + ' 条真实招聘数据的多因素加权预测'">
          <template #extra>
            <div class="predicted-salary-value">¥{{ (predictedSalary as number)?.toLocaleString() }}<span class="salary-unit-label">元/月</span></div>
            <div class="predict-factors" v-if="salaryFactors.length > 0">
              <p>影响因子：</p>
              <el-tag v-for="(f, i) in salaryFactors" :key="i" :type="f.type as any" effect="plain" style="margin:2px">{{ f.label }}</el-tag>
            </div>
          </template>
        </el-result>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from 'vue';

interface Factor { label: string; type: string }

defineOptions({ inheritAttrs: false });

const props = defineProps<{
  visible: boolean; loading: boolean;
  predictedSalary: number | null;
  salaryFactors: Factor[];
  totalJobs?: number | null;
  initCity?: string; initEducation?: string; initExperience?: string;
}>();

const emit = defineEmits<{
  'update:visible': [value: boolean];
  predict: [form: { city: string; experience: string; education: string; skills: string }]
}>();

const dialogVisible = computed({
  get: () => props.visible,
  set: (val) => emit('update:visible', val)
});

const form = reactive({ city: '', experience: '', education: '', skills: '' });

watch(() => props.visible, (val) => {
  if (val) {
    form.city = props.initCity || '';
    form.education = props.initEducation || '';
    form.experience = props.initExperience || '';
    form.skills = '';
  }
});

const handlePredict = () => {
  emit('predict', { ...form });
};
</script>

<style scoped>
.predict-result { margin-top: 20px; padding: 16px; background: #f8f9fc; border-radius: 12px; }
.predicted-salary-value { font-size: 36px; font-weight: 800; color: #e6a23c; text-align: center; margin: 12px 0; }
.salary-unit-label { font-size: 14px; font-weight: 400; color: var(--text-secondary); margin-left: 4px; }
.predict-factors { text-align: center; margin-top: 8px; }
.predict-factors p { color: var(--text-regular); font-size: 13px; margin-bottom: 8px; }
</style>
