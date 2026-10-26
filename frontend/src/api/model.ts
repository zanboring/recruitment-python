import http from './http';

export interface ModelStatus {
  ollamaAvailable: boolean;
  zhipuAvailable: boolean;
  currentModel: string;
  modelList: string[];
}

export interface ModelInfo {
  name: string;
  provider: string;
  available: boolean;
  description: string;
}

export function getModelStatus(): Promise<ModelStatus> {
  return http.get('/model/status');
}

export function switchModel(modelName: string): Promise<void> {
  return http.post('/model/switch', { modelName });
}

export function getModelList(): Promise<ModelInfo[]> {
  return http.get('/model/list');
}

export function reloadModel(): Promise<void> {
  return http.post('/model/reload');
}