import http from './http';

export interface KnowledgeBase {
  id: number;
  question: string;
  answer: string;
  source: string;
  status: number;
  qualityScore: number;
  usageCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface KnowledgeListResult {
  list: KnowledgeBase[];
  total: number;
  page: number;
  size: number;
}

export interface KnowledgeStats {
  total: number;
  enabled: number;
  disabled: number;
  manualCount: number;
  zhipuCount: number;
  ollamaCount: number;
  totalUsage: number;
}

export function fetchKnowledgeList(page: number = 1, size: number = 20): Promise<KnowledgeListResult> {
  return http.get('/knowledge/list', { params: { page, size } });
}

export function fetchAllKnowledge(): Promise<KnowledgeBase[]> {
  return http.get('/knowledge/all');
}

export function searchKnowledge(keyword: string): Promise<KnowledgeBase[]> {
  return http.get('/knowledge/search', { params: { keyword } });
}

export function getKnowledgeById(id: number): Promise<KnowledgeBase> {
  return http.get(`/knowledge/${id}`);
}

export function addKnowledge(data: { question: string; answer: string }): Promise<KnowledgeBase> {
  return http.post('/knowledge', data);
}

export function updateKnowledge(id: number, data: { question: string; answer: string; status?: number }): Promise<void> {
  return http.put(`/knowledge/${id}`, data);
}

export function deleteKnowledge(id: number): Promise<void> {
  return http.delete(`/knowledge/${id}`);
}

export function setKnowledgeStatus(id: number, status: number): Promise<void> {
  return http.put(`/knowledge/${id}/status?status=${status}`);
}

export function setKnowledgeScore(id: number, score: number): Promise<void> {
  return http.put(`/knowledge/${id}/score`, { params: { score } });
}

export function learnKnowledge(data: { question: string; answer: string }): Promise<KnowledgeBase> {
  return http.post('/knowledge/learn', data);
}

export function previewKnowledgeContext(question: string): Promise<string> {
  return http.get('/knowledge/preview', { params: { question } });
}

export function fetchKnowledgeStats(): Promise<KnowledgeStats> {
  return http.get('/knowledge/stats');
}