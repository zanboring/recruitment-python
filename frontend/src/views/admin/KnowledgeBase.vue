<template>
  <div class="knowledge-base">
    <div class="page-header">
      <h2>知识库管理</h2>
      <div class="header-actions">
        <el-button type="primary" @click="showAddDialog = true">
          <el-icon-plus /> 添加知识
        </el-button>
      </div>
    </div>

    <div class="stats-card">
      <div class="stat-item">
        <span class="stat-value">{{ stats.total }}</span>
        <span class="stat-label">总知识数</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">{{ stats.enabled }}</span>
        <span class="stat-label">已启用</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">{{ stats.totalUsage }}</span>
        <span class="stat-label">总使用次数</span>
      </div>
    </div>

    <div class="search-bar">
      <el-input
        v-model="searchKeyword"
        placeholder="搜索问题..."
        @keyup.enter="handleSearch"
        clearable
      >
        <template #append>
          <el-button @click="handleSearch">搜索</el-button>
        </template>
      </el-input>
    </div>

    <el-table :data="knowledgeList" border :loading="loading">
      <el-table-column prop="id" label="ID" width="80" />
      <el-table-column prop="question" label="问题" min-width="200" show-overflow-tooltip />
      <el-table-column prop="answer" label="回答" min-width="300" show-overflow-tooltip />
      <el-table-column prop="source" label="来源" width="100">
        <template #default="scope">
          <el-tag :type="getSourceType(scope.row.source)">
            {{ getSourceName(scope.row.source) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="80">
        <template #default="scope">
          <el-switch
            :value="scope.row.status === 1"
            @change="toggleStatus(scope.row.id, $event)"
          />
        </template>
      </el-table-column>
      <el-table-column prop="qualityScore" label="评分" width="80">
        <template #default="scope">
          <el-rate :value="scope.row.qualityScore" disabled :max="3" />
        </template>
      </el-table-column>
      <el-table-column prop="usageCount" label="使用次数" width="100" />
      <el-table-column prop="createdAt" label="创建时间" width="180" />
      <el-table-column label="操作" width="180">
        <template #default="scope">
          <el-button size="small" @click="editKnowledge(scope.row)">编辑</el-button>
          <el-button size="small" type="danger" @click="deleteKnowledge(scope.row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-if="total > 0"
      :total="total"
      :page-size="pageSize"
      :current-page="currentPage"
      @current-change="handlePageChange"
      layout="prev, pager, next, jumper"
    />

    <el-dialog :title="isEditing ? '编辑知识' : '添加知识'" v-model="showAddDialog" width="600px">
      <el-form :model="formData" label-width="80px">
        <el-form-item label="问题" required>
          <el-input v-model="formData.question" type="textarea" :rows="3" placeholder="请输入问题" />
        </el-form-item>
        <el-form-item label="回答" required>
          <el-input v-model="formData.answer" type="textarea" :rows="5" placeholder="请输入回答" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddDialog = false">取消</el-button>
        <el-button type="primary" @click="saveKnowledge">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, reactive } from 'vue';
import {
  fetchKnowledgeList,
  fetchKnowledgeStats,
  addKnowledge,
  updateKnowledge,
  deleteKnowledge as deleteKB,
  setKnowledgeStatus,
  searchKnowledge,
  type KnowledgeBase,
  type KnowledgeStats
} from '@/api/knowledge';

const loading = ref(false);
const currentPage = ref(1);
const pageSize = ref(20);
const total = ref(0);
const knowledgeList = ref<KnowledgeBase[]>([]);
const stats = ref<KnowledgeStats>({
  total: 0,
  enabled: 0,
  disabled: 0,
  manualCount: 0,
  zhipuCount: 0,
  ollamaCount: 0,
  totalUsage: 0
});
const searchKeyword = ref('');
const showAddDialog = ref(false);
const isEditing = ref(false);
const formData = reactive({
  id: 0,
  question: '',
  answer: ''
});

const loadData = async () => {
  loading.value = true;
  try {
    const result = await fetchKnowledgeList(currentPage.value, pageSize.value);
    knowledgeList.value = result.list;
    total.value = result.total;
  } finally {
    loading.value = false;
  }
};

const loadStats = async () => {
  const result = await fetchKnowledgeStats();
  stats.value = result;
};

const handleSearch = async () => {
  loading.value = true;
  try {
    if (searchKeyword.value.trim()) {
      const result = await searchKnowledge(searchKeyword.value);
      knowledgeList.value = result;
      total.value = result.length;
    } else {
      await loadData();
    }
  } finally {
    loading.value = false;
  }
};

const handlePageChange = (page: number) => {
  currentPage.value = page;
  loadData();
};

const editKnowledge = (row: KnowledgeBase) => {
  isEditing.value = true;
  formData.id = row.id;
  formData.question = row.question;
  formData.answer = row.answer;
  showAddDialog.value = true;
};

const saveKnowledge = async () => {
  if (!formData.question.trim() || !formData.answer.trim()) {
    alert('请填写完整信息');
    return;
  }

  try {
    if (isEditing.value) {
      await updateKnowledge(formData.id, { question: formData.question, answer: formData.answer });
    } else {
      await addKnowledge({ question: formData.question, answer: formData.answer });
    }
    showAddDialog.value = false;
    formData.id = 0;
    formData.question = '';
    formData.answer = '';
    isEditing.value = false;
    await loadData();
    await loadStats();
  } catch (error) {
    console.error('保存失败:', error);
  }
};

const deleteKnowledge = async (id: number) => {
  if (!confirm('确定删除这条知识吗？')) return;
  try {
    await deleteKB(id);
    await loadData();
    await loadStats();
  } catch (error) {
    console.error('删除失败:', error);
  }
};

const toggleStatus = async (id: number, value: boolean) => {
  try {
    await setKnowledgeStatus(id, value ? 1 : 0);
    await loadStats();
  } catch (error) {
    console.error('更新状态失败:', error);
  }
};

const getSourceType = (source: string) => {
  const types: Record<string, string> = {
    manual: 'primary',
    zhipu: 'success',
    ollama: 'warning'
  };
  return types[source] || 'default';
};

const getSourceName = (source: string) => {
  const names: Record<string, string> = {
    manual: '手动添加',
    zhipu: '智谱学习',
    ollama: 'Ollama学习'
  };
  return names[source] || source;
};

onMounted(() => {
  loadData();
  loadStats();
});
</script>

<style scoped>
.knowledge-base {
  padding: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.header-actions {
  display: flex;
  gap: 10px;
}

.stats-card {
  display: flex;
  gap: 20px;
  margin-bottom: 20px;
}

.stat-item {
  flex: 1;
  background: #fff;
  padding: 20px;
  border-radius: 8px;
  text-align: center;
}

.stat-value {
  display: block;
  font-size: 28px;
  font-weight: bold;
  color: #409eff;
}

.stat-label {
  display: block;
  font-size: 14px;
  color: #666;
  margin-top: 5px;
}

.search-bar {
  margin-bottom: 20px;
}
</style>