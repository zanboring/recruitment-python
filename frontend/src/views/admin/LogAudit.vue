<template>
  <div class="log-audit">
    <div class="page-header">
      <h2>操作日志审计</h2>
      <p>管理员专用 - 查看系统操作记录</p>
    </div>

    <div class="filter-section">
      <div class="filter-row">
        <div class="filter-item">
          <label>用户名</label>
          <input v-model="filters.username" type="text" placeholder="输入用户名" class="filter-input" />
        </div>
        <div class="filter-item">
          <label>操作类型</label>
          <select v-model="filters.type" class="filter-select">
            <option value="">全部</option>
            <option value="登录">登录</option>
            <option value="登出">登出</option>
            <option value="新增">新增</option>
            <option value="修改">修改</option>
            <option value="删除">删除</option>
            <option value="查询">查询</option>
            <option value="导入">导入</option>
            <option value="导出">导出</option>
          </select>
        </div>
        <div class="filter-item">
          <label>开始时间</label>
          <input v-model="filters.startTime" type="datetime-local" class="filter-input" />
        </div>
        <div class="filter-item">
          <label>结束时间</label>
          <input v-model="filters.endTime" type="datetime-local" class="filter-input" />
        </div>
        <div class="filter-actions">
          <button class="search-btn" @click="loadLogs">查询</button>
          <button class="reset-btn" @click="resetFilters">重置</button>
          <button class="export-btn" @click="exportLogs">导出Excel</button>
        </div>
      </div>
    </div>

    <div class="table-container">
      <table class="log-table">
        <thead>
          <tr>
            <th>日志ID</th>
            <th>操作人</th>
            <th>操作类型</th>
            <th>操作描述</th>
            <th>请求方法</th>
            <th>请求路径</th>
            <th>客户端IP</th>
            <th>操作时间</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="log in logList" :key="log.id">
            <td>{{ log.id }}</td>
            <td>{{ log.username || '未知' }}</td>
            <td>
              <span :class="['type-badge', getTypeClass(log.action)]">
                {{ log.action }}
              </span>
            </td>
            <td class="description-cell">{{ log.params || '-' }}</td>
            <td>
              <span :class="['method-badge', log.method?.toLowerCase()]">
                {{ log.method || '-' }}
              </span>
            </td>
            <td class="path-cell">{{ log.uri }}</td>
            <td>{{ log.ip }}</td>
            <td>{{ formatDate(log.createdAt) }}</td>
            <td>
              <span :class="['status-badge', log.success ? 'success' : 'error']">
                {{ log.success ? '成功' : '失败' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="logList.length === 0" class="empty-state">
        <p>暂无操作日志记录</p>
      </div>
    </div>

    <div class="pagination" v-if="total > pageSize">
      <button
        class="pagination-btn"
        :disabled="currentPage === 0"
        @click="prevPage"
      >
        上一页
      </button>
      <span class="pagination-info">
        第 {{ currentPage + 1 }} / {{ totalPages }} 页
      </span>
      <button
        class="pagination-btn"
        :disabled="currentPage >= totalPages - 1"
        @click="nextPage"
      >
        下一页
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue';
import http from '@/api/http';

const filters = reactive({
  username: '',
  type: '',
  startTime: '',
  endTime: '',
});

const logList = ref<any[]>([]);
const currentPage = ref(0);
const pageSize = ref(20);
const total = ref(0);

const totalPages = computed(() => Math.ceil(total.value / pageSize.value));

interface LogPageVO {
  list: any[];
  total: number;
  pageNum: number;
  pageSize: number;
}

const loadLogs = async () => {
  try {
    const params: any = {
      page: currentPage.value + 1,
      size: pageSize.value,
    };
    
    if (filters.username) params.username = filters.username;
    if (filters.type) params.type = filters.type;
    if (filters.startTime) params.startTime = filters.startTime;
    if (filters.endTime) params.endTime = filters.endTime;
    
    const response = await http.get('/logs/list', { params }) as LogPageVO;
    logList.value = response.list;
    total.value = response.total;
  } catch (error) {
    console.error('加载日志失败:', error);
  }
};

const resetFilters = () => {
  filters.username = '';
  filters.type = '';
  filters.startTime = '';
  filters.endTime = '';
  currentPage.value = 0;
  loadLogs();
};

const exportLogs = async () => {
  try {
    const params: any = {};
    if (filters.username) params.username = filters.username;
    if (filters.type) params.type = filters.type;
    if (filters.startTime) params.startTime = filters.startTime;
    if (filters.endTime) params.endTime = filters.endTime;
    
    const blobData = await http.get('/logs/export', {
      params,
      responseType: 'blob' as any
    }) as Blob;
    
    const blob = new Blob([blobData], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `操作日志_${new Date().toLocaleDateString('zh-CN').replace(/\//g, '-')}.xlsx`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  } catch (error) {
    console.error('导出日志失败:', error);
  }
};

const getTypeClass = (type: string) => {
  const typeMap: Record<string, string> = {
    '登录': 'login',
    '登出': 'logout',
    '新增': 'create',
    '修改': 'update',
    '删除': 'delete',
    '查询': 'query',
    '导入': 'import',
    '导出': 'export',
  };
  return typeMap[type] || 'other';
};

const formatDate = (dateStr: string) => {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
};

const prevPage = () => {
  if (currentPage.value > 0) {
    currentPage.value--;
    loadLogs();
  }
};

const nextPage = () => {
  if (currentPage.value < totalPages.value - 1) {
    currentPage.value++;
    loadLogs();
  }
};

onMounted(() => {
  loadLogs();
});
</script>

<style scoped>
.log-audit {
  padding: 20px;
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0 0 8px 0;
  color: #333;
}

.page-header p {
  margin: 0;
  color: #666;
  font-size: 14px;
}

.filter-section {
  background: #f8f9fa;
  padding: 16px 20px;
  border-radius: 8px;
  margin-bottom: 20px;
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
}

.filter-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.filter-item label {
  font-size: 13px;
  color: #666;
}

.filter-input,
.filter-select {
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 13px;
  min-width: 150px;
}

.filter-actions {
  display: flex;
  gap: 10px;
  margin-left: auto;
}

.search-btn {
  padding: 8px 16px;
  background: #409eff;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.search-btn:hover {
  background: #66b1ff;
}

.reset-btn {
  padding: 8px 16px;
  background: #eee;
  color: #666;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.reset-btn:hover {
  background: #ddd;
}

.export-btn {
  padding: 8px 16px;
  background: #67c23a;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.export-btn:hover {
  background: #85ce61;
}

.table-container {
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.log-table {
  width: 100%;
  border-collapse: collapse;
}

.log-table th,
.log-table td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid #eee;
  font-size: 13px;
}

.log-table th {
  background: #f8f9fa;
  font-weight: 600;
  color: #333;
}

.type-badge {
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 11px;
}

.type-badge.login { background: #95de64; color: #23541e; }
.type-badge.logout { background: #ffccc7; color: #d93026; }
.type-badge.create { background: #67c23a; color: white; }
.type-badge.update { background: #e6a23c; color: white; }
.type-badge.delete { background: #f56c6c; color: white; }
.type-badge.query { background: #909399; color: white; }
.type-badge.import { background: #409eff; color: white; }
.type-badge.export { background: #b37feb; color: white; }
.type-badge.other { background: #d3d4d5; color: #666; }

.method-badge {
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}

.method-badge.get { background: #67c23a; color: white; }
.method-badge.post { background: #409eff; color: white; }
.method-badge.put { background: #e6a23c; color: white; }
.method-badge.delete { background: #f56c6c; color: white; }
.method-badge.patch { background: #b37feb; color: white; }

.status-badge {
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 11px;
}

.status-badge.success { background: #95de64; color: #23541e; }
.status-badge.error { background: #ffccc7; color: #d93026; }

.description-cell {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.path-cell {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-state {
  padding: 40px;
  text-align: center;
  color: #999;
}

.pagination {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 16px;
  margin-top: 20px;
}

.pagination-btn {
  padding: 8px 16px;
  border: 1px solid #ddd;
  background: white;
  border-radius: 4px;
  cursor: pointer;
}

.pagination-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pagination-info {
  color: #666;
}
</style>
