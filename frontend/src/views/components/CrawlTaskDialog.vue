<template>
  <el-dialog
    v-model="visible"
    title="创建爬虫任务"
    width="500px"
    :close-on-click-modal="false"
  >
    <el-form :model="crawlForm" label-width="100px">
      <el-form-item label="来源网站">
        <el-select
          v-model="crawlForm.sourceSite"
          placeholder="请选择来源网站"
          style="width: 100%"
          :loading="optionsLoading"
        >
          <el-option
            v-for="p in platformOptions"
            :key="p.value"
            :label="p.implemented ? p.label : `${p.label}（暂未支持采集）`"
            :value="p.value"
            :disabled="!p.implemented"
          />
        </el-select>
        <div class="field-hint" v-if="optionsLoaded && unimplementedPlatforms.length">
          当前已实现采集：{{ implementedPlatforms.map(p => p.label).join('、') }}；
          {{ unimplementedPlatforms.map(p => p.label).join('、') }} 尚未实现，无法选择。
        </div>
      </el-form-item>
      <el-form-item label="关键词">
        <el-input v-model="crawlForm.keyword" placeholder="请输入关键词，如：Java开发" />
      </el-form-item>
      <el-form-item label="城市">
        <div class="city-selection">
          <div class="city-actions">
            <el-button type="primary" size="small" @click="selectAllCities">全选</el-button>
            <el-button type="info" size="small" @click="selectInverseCities">反选</el-button>
            <el-button type="danger" size="small" @click="clearCities">清空</el-button>
          </div>
          <el-checkbox-group v-model="crawlForm.cities" style="margin-top: 10px">
            <el-checkbox v-for="city in cityOptions" :key="city" :value="city" style="margin-right: 15px; margin-bottom: 10px;">{{ city }}</el-checkbox>
          </el-checkbox-group>
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" @click="createCrawlTask" :loading="creatingTask">
        创建任务
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue';
import { ElMessage } from 'element-plus';
import http from '@/api/http';
import { fetchCrawlOptions, type CrawlPlatformOption } from '@/api/crawl';

// 兜底选项：**仅在**「可选项接口拉取失败」时生效，保证对话框仍然可用，而不是
// 给出一个空下拉框让用户没法创建任务（例如对接的后端尚未提供该接口）。
// 正常路径一律以后端返回为准；兜底值刻意只放已实现的平台与常用城市，
// 这样即使降级也不会让用户选到注定失败的可选项。
const FALLBACK_PLATFORMS: CrawlPlatformOption[] = [
  { value: 'boss', label: 'BOSS直聘', implemented: true }
];
const FALLBACK_CITIES = [
  '北京', '上海', '广州', '深圳', '长沙', '武汉',
  '成都', '重庆', '杭州', '南京', '西安'
];

const platformOptions = ref<CrawlPlatformOption[]>([...FALLBACK_PLATFORMS]);
const cityOptions = ref<string[]>([...FALLBACK_CITIES]);
const optionsLoading = ref(false);
const optionsLoaded = ref(false);

const implementedPlatforms = computed(() => platformOptions.value.filter(p => p.implemented));
const unimplementedPlatforms = computed(() => platformOptions.value.filter(p => !p.implemented));

const loadOptions = async () => {
  optionsLoading.value = true;
  try {
    const data = await fetchCrawlOptions();
    if (data?.platforms?.length) platformOptions.value = data.platforms;
    if (data?.cities?.length) cityOptions.value = data.cities;
    optionsLoaded.value = true;
  } catch {
    // 拉取失败不阻塞使用：保留兜底选项，用户仍能提交已实现的平台
    optionsLoaded.value = false;
  } finally {
    optionsLoading.value = false;
  }
};

// Props & Emits
const visible = defineModel<boolean>('visible', { default: false });

// 每次打开时刷新：后端新增/停用平台后，前端无需发版即可生效
watch(visible, (open) => {
  if (open) loadOptions();
});
defineProps<{
  creatingTask: boolean;
}>();

const emit = defineEmits<{
  (e: 'created'): void;
}>();

const crawlForm = reactive({
  sourceSite: '',
  keyword: '',
  cities: [] as string[]
});

const submitLoading = ref(false);

const selectAllCities = () => {
  crawlForm.cities = [...cityOptions.value];
};

const selectInverseCities = () => {
  crawlForm.cities = cityOptions.value.filter(city => !crawlForm.cities.includes(city));
};

const clearCities = () => {
  crawlForm.cities = [];
};

const createCrawlTask = async () => {
  if (submitLoading.value) return;

  if (!crawlForm.sourceSite || !crawlForm.keyword || crawlForm.cities.length === 0) {
    ElMessage.warning('请填写完整的任务信息');
    return;
  }

  // 防御性校验：未实现的平台在界面上已置灰，但下拉框的值仍可能被程序改写。
  // 提交一个后端没实现的平台，只会换来一个「失败」任务 —— 不如在这里就拦住。
  const platform = platformOptions.value.find(p => p.value === crawlForm.sourceSite);
  if (!platform || !platform.implemented) {
    ElMessage.warning('请选择已实现采集的来源网站');
    return;
  }

  submitLoading.value = true;
  try {
    await http.post('/crawl/task', {
      ...crawlForm,
      city: crawlForm.cities.join(',')
    });
    ElMessage.success('任务创建成功');
    visible.value = false;
    Object.assign(crawlForm, { sourceSite: '', keyword: '', cities: [] });
    emit('created');
  } catch (error: unknown) {
    ElMessage.error('任务创建失败');
  } finally {
    submitLoading.value = false;
  }
};
</script>

<style scoped>
/* 选项来源的说明文字：让「哪些平台能用」这件事对用户可见，
   而不是等他创建完任务后从「失败」标签里去猜 */
.field-hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: #909399;
}

.city-selection {
  width: 100%;
}

.city-actions {
  display: flex;
  gap: 10px;
  margin-bottom: 10px;
}

.city-actions .el-button {
  flex: 1;
  max-width: 100px;
}

.el-checkbox-group {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.el-checkbox {
  margin-right: 15px !important;
  margin-bottom: 10px !important;
}
</style>
