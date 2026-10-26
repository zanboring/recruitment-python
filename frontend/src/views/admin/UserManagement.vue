<template>
  <div class="user-management">
    <div class="page-header">
      <h2>用户管理</h2>
      <p>管理员专用 - 管理系统用户账户</p>
    </div>

    <div class="search-bar">
      <input
        v-model="searchKeyword"
        type="text"
        placeholder="搜索用户名..."
        class="search-input"
        @keyup.enter="loadUsers"
      />
      <button class="search-btn" @click="loadUsers">搜索</button>
    </div>

    <div class="table-container">
      <table class="user-table">
        <thead>
          <tr>
            <th>用户ID</th>
            <th>用户名</th>
            <th>角色</th>
            <th>邮箱</th>
            <th>状态</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in userList" :key="user.id">
            <td>{{ user.id }}</td>
            <td>{{ user.username }}</td>
            <td>
              <span :class="['role-badge', user.role.toLowerCase()]">
                {{ user.role === 'ADMIN' ? '管理员' : '普通用户' }}
              </span>
            </td>
            <td>{{ user.email || '-' }}</td>
            <td>
              <span :class="['status-badge', user.enabled ? 'enabled' : 'disabled']">
                {{ user.enabled ? '启用' : '禁用' }}
              </span>
            </td>
            <td>{{ formatDate(user.createdAt) }}</td>
            <td class="action-cell">
              <button class="action-btn edit-btn" @click="editUser(user)">编辑</button>
              <button
                class="action-btn"
                :class="user.enabled ? 'disable-btn' : 'enable-btn'"
                @click="toggleStatus(user.id, !user.enabled)"
              >
                {{ user.enabled ? '禁用' : '启用' }}
              </button>
              <button class="action-btn delete-btn" @click="deleteUser(user.id)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="userList.length === 0" class="empty-state">
        <p>暂无用户数据</p>
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

    <div v-if="showModal" class="modal-overlay" @click.self="closeModal">
      <div class="modal-content">
        <div class="modal-header">
          <h3>{{ isEdit ? '编辑用户' : '添加用户' }}</h3>
          <button class="close-btn" @click="closeModal">×</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label>用户名</label>
            <input v-model="formData.username" type="text" class="form-input" />
          </div>
          <div class="form-group">
            <label>邮箱</label>
            <input v-model="formData.email" type="email" class="form-input" />
          </div>
          <div class="form-group">
            <label>角色</label>
            <select v-model="formData.role" class="form-select">
              <option value="USER">普通用户</option>
              <option value="ADMIN">管理员</option>
            </select>
          </div>
          <div class="form-group">
            <label>技能（逗号分隔）</label>
            <input v-model="formData.skills" type="text" class="form-input" placeholder="Java,Spring,MySQL" />
          </div>
          <div class="form-group">
            <label>学历</label>
            <select v-model="formData.education" class="form-select">
              <option value="">请选择</option>
              <option value="大专">大专</option>
              <option value="本科">本科</option>
              <option value="硕士">硕士</option>
              <option value="博士">博士</option>
            </select>
          </div>
          <div class="form-group">
            <label>工作年限</label>
            <input v-model="formData.experienceYears" type="number" class="form-input" min="0" />
          </div>
        </div>
        <div class="modal-footer">
          <button class="modal-btn cancel-btn" @click="closeModal">取消</button>
          <button class="modal-btn confirm-btn" @click="saveUser">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue';
import { userApi } from '@/api/user';

const searchKeyword = ref('');
const userList = ref<any[]>([]);
const currentPage = ref(0);
const pageSize = ref(10);
const total = ref(0);

const showModal = ref(false);
const isEdit = ref(false);
const editingId = ref<number | null>(null);

const formData = reactive({
  username: '',
  email: '',
  role: 'USER',
  skills: '',
  education: '',
  experienceYears: null as number | null,
});

const totalPages = computed(() => Math.ceil(total.value / pageSize.value));

const loadUsers = async () => {
  try {
    const response = await userApi.listUsers(searchKeyword.value, currentPage.value + 1, pageSize.value);
    userList.value = response.list;
    total.value = response.total;
    // 同步后端返回的实际页码
    if (response.pageNum != null) {
      currentPage.value = response.pageNum - 1;
    }
  } catch (error) {
    console.error('加载用户列表失败:', error);
  }
};

const editUser = (user: any) => {
  isEdit.value = true;
  editingId.value = user.id;
  formData.username = user.username;
  formData.email = user.email || '';
  formData.role = user.role;
  formData.skills = user.skills || '';
  formData.education = user.education || '';
  formData.experienceYears = user.experienceYears || null;
  showModal.value = true;
};

const closeModal = () => {
  showModal.value = false;
  isEdit.value = false;
  editingId.value = null;
  formData.username = '';
  formData.email = '';
  formData.role = 'USER';
  formData.skills = '';
  formData.education = '';
  formData.experienceYears = null;
};

const saveUser = async () => {
  try {
    if (isEdit.value && editingId.value) {
      await userApi.updateUser(editingId.value, {
        username: formData.username,
        email: formData.email,
        role: formData.role,
        skills: formData.skills,
        education: formData.education,
        experienceYears: formData.experienceYears ?? undefined,
      });
    }
    closeModal();
    loadUsers();
  } catch (error) {
    console.error('保存用户失败:', error);
  }
};

const toggleStatus = async (id: number, enabled: boolean) => {
  try {
    await userApi.toggleUserStatus(id, enabled);
    loadUsers();
  } catch (error) {
    console.error('切换用户状态失败:', error);
  }
};

const deleteUser = async (id: number) => {
  if (!confirm('确定要删除该用户吗？')) return;
  try {
    await userApi.deleteUser(id);
    loadUsers();
  } catch (error) {
    console.error('删除用户失败:', error);
  }
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
  });
};

const prevPage = () => {
  if (currentPage.value > 0) {
    currentPage.value--;
    loadUsers();
  }
};

const nextPage = () => {
  if (currentPage.value < totalPages.value - 1) {
    currentPage.value++;
    loadUsers();
  }
};

onMounted(() => {
  loadUsers();
});
</script>

<style scoped>
.user-management {
  padding: 20px;
  max-width: 1200px;
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

.search-bar {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.search-input {
  flex: 1;
  max-width: 300px;
  padding: 10px 14px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
}

.search-btn {
  padding: 10px 20px;
  background: #409eff;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}

.search-btn:hover {
  background: #66b1ff;
}

.table-container {
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.user-table {
  width: 100%;
  border-collapse: collapse;
}

.user-table th,
.user-table td {
  padding: 12px 16px;
  text-align: left;
  border-bottom: 1px solid #eee;
}

.user-table th {
  background: #f8f9fa;
  font-weight: 600;
  color: #333;
}

.role-badge {
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
}

.role-badge.admin {
  background: #ffc069;
  color: #8b4513;
}

.role-badge.user {
  background: #95de64;
  color: #23541e;
}

.status-badge {
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
}

.status-badge.enabled {
  background: #95de64;
  color: #23541e;
}

.status-badge.disabled {
  background: #ffccc7;
  color: #d93026;
}

.action-cell {
  display: flex;
  gap: 8px;
}

.action-btn {
  padding: 6px 12px;
  border: none;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
}

.edit-btn {
  background: #67c23a;
  color: white;
}

.edit-btn:hover {
  background: #85ce61;
}

.disable-btn {
  background: #e6a23c;
  color: white;
}

.disable-btn:hover {
  background: #f0c78a;
}

.enable-btn {
  background: #67c23a;
  color: white;
}

.enable-btn:hover {
  background: #85ce61;
}

.delete-btn {
  background: #f56c6c;
  color: white;
}

.delete-btn:hover {
  background: #f89898;
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

.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
}

.modal-content {
  background: white;
  border-radius: 8px;
  width: 90%;
  max-width: 500px;
  overflow: hidden;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #eee;
}

.modal-header h3 {
  margin: 0;
}

.close-btn {
  font-size: 24px;
  background: none;
  border: none;
  cursor: pointer;
  color: #999;
}

.modal-body {
  padding: 20px;
}

.form-group {
  margin-bottom: 16px;
}

.form-group label {
  display: block;
  margin-bottom: 6px;
  font-weight: 500;
}

.form-input,
.form-select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 14px;
  box-sizing: border-box;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 16px 20px;
  border-top: 1px solid #eee;
}

.modal-btn {
  padding: 10px 20px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.cancel-btn {
  background: #eee;
  color: #666;
}

.cancel-btn:hover {
  background: #ddd;
}

.confirm-btn {
  background: #409eff;
  color: white;
}

.confirm-btn:hover {
  background: #66b1ff;
}
</style>
