<template>
  <div class="model-manager">
    <div class="page-header">
      <h2>AI模型管理</h2>
    </div>

    <div class="model-status-card">
      <div class="status-header">
        <span class="status-title">当前模型状态</span>
        <span class="current-model">当前使用: {{ status.currentModel || '未选择' }}</span>
      </div>

      <div class="model-list">
        <div
          v-for="model in modelList"
          :key="model.name"
          class="model-card"
          :class="{ active: status.currentModel === model.name, disabled: !model.available }"
          @click="selectModel(model)"
        >
          <div class="model-icon">
            <el-icon v-if="model.provider === 'ollama'"><Cpu /></el-icon>
            <el-icon v-else><Cloudy /></el-icon>
          </div>
          <div class="model-info">
            <div class="model-name">{{ model.name }}</div>
            <div class="model-provider">{{ model.provider === 'ollama' ? '本地模型' : '云端模型' }}</div>
          </div>
          <div class="model-status">
            <el-badge :type="model.available ? 'success' : 'warning'" :value="model.available ? '可用' : '不可用'" />
          </div>
        </div>
      </div>
    </div>

    <div class="model-detail">
      <h3>模型状态详情</h3>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="Ollama服务">
          <span :class="status.ollamaAvailable ? 'status-available' : 'status-unavailable'">
            {{ status.ollamaAvailable ? '✓ 可用' : '✗ 不可用' }}
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="智谱API">
          <span :class="status.zhipuAvailable ? 'status-available' : 'status-unavailable'">
            {{ status.zhipuAvailable ? '✓ 可用' : '✗ 不可用' }}
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="当前模型" :span="2">
          <code>{{ status.currentModel || '-' }}</code>
        </el-descriptions-item>
      </el-descriptions>

      <div class="action-buttons">
        <el-button type="primary" @click="refreshStatus" :loading="refreshing">
          <el-icon><Refresh /></el-icon>
          刷新状态
        </el-button>
        <el-button type="warning" @click="reloadModel" :loading="reloading">
          <el-icon><RefreshRight /></el-icon>
          重载模型
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import { Cpu, Cloudy, Refresh, RefreshRight } from '@element-plus/icons-vue';
import {
  getModelStatus,
  getModelList,
  switchModel,
  reloadModel as reloadModelApi,
  type ModelStatus,
  type ModelInfo
} from '@/api/model';

const loading = ref(false);
const refreshing = ref(false);
const reloading = ref(false);

const status = ref<ModelStatus>({
  ollamaAvailable: false,
  zhipuAvailable: false,
  currentModel: '',
  modelList: []
});

const modelList = ref<ModelInfo[]>([]);

const loadStatus = async () => {
  loading.value = true;
  try {
    const [statusRes, listRes] = await Promise.all([
      getModelStatus(),
      getModelList()
    ]);
    status.value = statusRes;
    modelList.value = listRes;
  } catch (error) {
    ElMessage.error('加载模型状态失败');
  } finally {
    loading.value = false;
  }
};

const selectModel = async (model: ModelInfo) => {
  if (!model.available || status.value.currentModel === model.name) return;

  try {
    await switchModel(model.name);
    ElMessage.success(`已切换到 ${model.name}`);
    await loadStatus();
  } catch (error) {
    ElMessage.error('切换模型失败');
  }
};

const refreshStatus = async () => {
  refreshing.value = true;
  try {
    await loadStatus();
    ElMessage.success('状态已刷新');
  } catch (error) {
    ElMessage.error('刷新失败');
  } finally {
    refreshing.value = false;
  }
};

const reloadModel = async () => {
  reloading.value = true;
  try {
    await reloadModelApi();
    ElMessage.success('模型已重载');
    await loadStatus();
  } catch (error) {
    ElMessage.error('重载失败');
  } finally {
    reloading.value = false;
  }
};

onMounted(() => {
  loadStatus();
});
</script>

<style scoped>
.model-manager {
  padding: 20px;
}

.page-header {
  margin-bottom: 20px;
}

.model-status-card {
  background: #fff;
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 20px;
}

.status-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.status-title {
  font-size: 16px;
  font-weight: 600;
}

.current-model {
  background: #e8f4fd;
  color: #1989fa;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 14px;
}

.model-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}

.model-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  border: 2px solid #e4e7ed;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.3s;
}

.model-card:hover {
  border-color: #c0c4cc;
}

.model-card.active {
  border-color: #409eff;
  background: #f0f7ff;
}

.model-card.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.model-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
  border-radius: 8px;
  font-size: 24px;
  color: #606266;
}

.model-card.active .model-icon {
  background: #e8f4fd;
  color: #409eff;
}

.model-info {
  flex: 1;
}

.model-name {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.model-provider {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.model-status {
  margin-left: auto;
}

.model-detail {
  background: #fff;
  border-radius: 8px;
  padding: 20px;
}

.model-detail h3 {
  margin-bottom: 16px;
  font-size: 16px;
}

.status-available {
  color: #67c23a;
  font-weight: 500;
}

.status-unavailable {
  color: #f56c6c;
  font-weight: 500;
}

.action-buttons {
  display: flex;
  gap: 12px;
  margin-top: 20px;
}
</style>