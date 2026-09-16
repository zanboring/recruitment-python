import http from './http';

export interface ProviderConfig {
  aiProvider?: string;        // deepseek | zhipu
  deepseekApiKey?: string;    // 脱敏展示：前4后4加星
  deepseekApiUrl?: string;
  deepseekModel?: string;
  zhipuaiApiKey?: string;
  zhipuaiApiUrl?: string;
  zhipuaiModel?: string;
  ollamaEnabled?: boolean;
  ollamaBaseUrl?: string;
  ollamaModel?: string;
  ollamaCodeModel?: string;
  ollamaToolModel?: string;
  editableKeys?: string[];
}

export interface ProviderStatus {
  written: string[];
  status: ProviderConfig;
}

/** 读取 AI 服务配置（密钥已脱敏） */
export function getProviderConfig(): Promise<ProviderConfig> {
  return http.get('/settings/provider');
}

/** 保存 AI 服务配置（只写白名单键；空值表示清除） */
export function saveProviderConfig(data: Partial<ProviderConfig>): Promise<ProviderStatus> {
  return http.post('/settings/provider', data);
}