import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
// [优化] 改用封装好的 http 实例，统一认证和错误处理
import http from '@/api/http';

export type ModelType = 'primary' | 'local' | 'auto';

interface OllamaStatus {
  available: boolean;
  model: string;
  status: string;
  description: string;
}

interface ZhipuStatus {
  available: boolean;
  model: string;
  description: string;
}

interface AIStatus {
  ollama: OllamaStatus;
  zhipu: ZhipuStatus;
}

export const useModelStore = defineStore('model', () => {
  const currentModel = ref<ModelType>('auto');
  
  const ollamaAvailable = ref(false);
  const ollamaModel = ref('');
  const ollamaStatus = ref('');
  const ollamaDescription = ref('');
  
  const zhipuAvailable = ref(false);
  const zhipuModel = ref('');
  const zhipuDescription = ref('');
  
  const loading = ref(false);

  const showSwitchButton = computed(() => {
    return ollamaAvailable.value || zhipuAvailable.value;
  });

  const currentModelName = computed(() => {
    switch (currentModel.value) {
      case 'local':
        return '本地模型';
      case 'primary':
        return '主API';
      case 'auto':
        return '自动';
      default:
        return '未知';
    }
  });

  async function fetchStatus() {
    loading.value = true;
    try {
      // [优化] 改用封装的 http 实例，自动携带 Authorization header
      const data: AIStatus = await http.get('/ai/status');
      
      if (data.ollama) {
        ollamaAvailable.value = data.ollama.available;
        ollamaModel.value = data.ollama.model;
        ollamaStatus.value = data.ollama.status;
        ollamaDescription.value = data.ollama.description;
      }
      
      if (data.zhipu) {
        zhipuAvailable.value = data.zhipu.available;
        zhipuModel.value = data.zhipu.model;
        zhipuDescription.value = data.zhipu.description;
      }
    } catch (error: any) {
      console.error('获取模型状态失败:', error);
      // [优化] 简化错误日志，移除冗余的嵌套判断
      ollamaAvailable.value = false;
      zhipuAvailable.value = false;
    } finally {
      loading.value = false;
    }
  }

  function switchToPrimary() {
    currentModel.value = 'primary';
  }

  function switchToLocal() {
    if (ollamaAvailable.value) {
      currentModel.value = 'local';
    }
  }

  function switchToAuto() {
    currentModel.value = 'auto';
  }

  function getUseLocalModelValue(): boolean | null {
    switch (currentModel.value) {
      case 'local':
        return true;
      case 'primary':
        return false;
      case 'auto':
        return null;
      default:
        return null;
    }
  }

  function refreshStatus() {
    fetchStatus();
  }

  return {
    currentModel,
    ollamaAvailable,
    ollamaModel,
    ollamaStatus,
    ollamaDescription,
    zhipuAvailable,
    zhipuModel,
    zhipuDescription,
    loading,
    showSwitchButton,
    currentModelName,
    fetchStatus,
    switchToPrimary,
    switchToLocal,
    switchToAuto,
    getUseLocalModelValue,
    refreshStatus
  };
});
