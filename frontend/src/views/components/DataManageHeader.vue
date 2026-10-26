<template>
  <el-card class="action-card" shadow="never">
    <div class="card-header">
      <div class="title">
        <el-icon><FolderOpened /></el-icon>
        <span>数据管理</span>
      </div>
    </div>

    <div class="action-buttons">
      <el-upload
        :show-file-list="false"
        :http-request="onUpload"
        accept=".xlsx"
        :auto-upload="false"
        :on-change="handleFileChange"
      >
        <el-button type="primary" size="large">
          <el-icon><Upload /></el-icon>
          导入招聘数据
        </el-button>
      </el-upload>

      <el-button type="success" size="large" @click="onExport">
        <el-icon><Download /></el-icon>
        导出招聘数据
      </el-button>

      <el-button type="danger" plain size="large" @click="cleanupDatabase" :loading="cleaningData">
        <el-icon><Delete /></el-icon>
        清洗数据库
      </el-button>

      <el-button type="primary" size="large" @click="handleQuickCrawl" :loading="quickCrawling" :disabled="quickCrawling">
        <el-icon><Connection /></el-icon>
        一键实时爬取
      </el-button>

      <el-button type="warning" size="large" @click="visionDialogVisible = true">
        <el-icon><PictureFilled /></el-icon>
        截图识别导入
      </el-button>

      <el-button type="primary" plain size="large" @click="urlDialogVisible = true">
        <el-icon><Link /></el-icon>
        链接导入
      </el-button>

      <el-button type="warning" size="large" @click="handleOpenCrawlDialog">
        <el-icon><Connection /></el-icon>
        创建爬虫任务
      </el-button>

      <el-button type="info" size="large" @click="handleRefreshData">
        <el-icon><Refresh /></el-icon>
        刷新数据
      </el-button>
    </div>
  </el-card>

    <!-- 视觉识别导入对话框 -->
    <el-dialog v-model="visionDialogVisible" title="截图识别导入" width="640px">
      <div class="vision-body">
        <el-upload
          drag
          :show-file-list="false"
          accept="image/*"
          :before-upload="beforeVisionUpload"
          :disabled="visionLoading"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽招聘截图到此处，或 <em>点击选择图片</em></div>
          <template #tip>
            <div class="el-upload__tip">支持 BOSS/智联等招聘详情页截图、海报截图；识别后需确认再入库</div>
          </template>
        </el-upload>

        <div v-if="visionLoading" class="vision-loading">
          <el-icon class="is-loading"><Loading /></el-icon>&nbsp;视觉模型识别中…
        </div>

        <el-descriptions v-else-if="visionResult" :column="2" border class="vision-result">
          <el-descriptions-item label="岗位">{{ visionResult.title || '-' }}</el-descriptions-item>
          <el-descriptions-item label="公司">{{ visionResult.company_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="城市">{{ visionResult.city || '-' }}</el-descriptions-item>
          <el-descriptions-item label="薪资">
            {{ visionResult.min_salary ? Math.round(visionResult.min_salary / 1000) + 'K' + (visionResult.max_salary ? '-' + Math.round(visionResult.max_salary / 1000) + 'K' : '') : '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="经验">{{ visionResult.experience || '-' }}</el-descriptions-item>
          <el-descriptions-item label="学历">{{ visionResult.education || '-' }}</el-descriptions-item>
          <el-descriptions-item label="技能" :span="2">{{ visionResult.skills || '-' }}</el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ visionResult.job_desc || '-' }}</el-descriptions-item>
        </el-descriptions>
      </div>
      <template #footer>
        <el-button @click="visionDialogVisible = false">取消</el-button>
        <el-button v-if="visionResult" type="primary" :loading="visionSaving" @click="confirmVisionSave">
          确认入库
        </el-button>
      </template>
    </el-dialog>

    <!-- 链接导入对话框 -->
    <el-dialog v-model="urlDialogVisible" title="链接导入（招聘详情页 URL）" width="560px">
      <el-input
        v-model="urlInput"
        placeholder="粘贴招聘详情页链接，如 https://www.zhipin.com/job_detail/xxxx.html"
        size="large"
        clearable
      />
      <div class="el-upload__tip" style="margin-top:8px">
        用 Playwright 打开页面 → 提取正文 → LLM 结构化；入库后系统会周期性核查该岗位是否还在
      </div>

      <el-descriptions v-if="urlResult" :column="2" border class="vision-result" style="margin-top:14px">
        <el-descriptions-item label="岗位">{{ urlResult.title || '-' }}</el-descriptions-item>
        <el-descriptions-item label="公司">{{ urlResult.company_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="城市">{{ urlResult.city || '-' }}</el-descriptions-item>
        <el-descriptions-item label="薪资">
          {{ urlResult.min_salary ? Math.round(urlResult.min_salary / 1000) + 'K' + (urlResult.max_salary ? '-' + Math.round(urlResult.max_salary / 1000) + 'K' : '') : '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="经验">{{ urlResult.experience || '-' }}</el-descriptions-item>
        <el-descriptions-item label="学历">{{ urlResult.education || '-' }}</el-descriptions-item>
        <el-descriptions-item label="技能" :span="2">{{ urlResult.skills || '-' }}</el-descriptions-item>
      </el-descriptions>

      <template #footer>
        <el-button @click="urlDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="urlLoading" :disabled="!urlInput.trim()" @click="doUrlImport(false)">
          识别预览
        </el-button>
        <el-button v-if="urlResult" type="success" :loading="urlSaving" @click="doUrlImport(true)">
          确认入库
        </el-button>
      </template>
    </el-dialog>
</template>
<script setup lang="ts">
import { ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  FolderOpened,
  Upload,
  UploadFilled,
  Download,
  Connection,
  Refresh,
  Delete,
  PictureFilled,
  Loading,
  Link
} from '@element-plus/icons-vue';
import http from '@/api/http';

// Props
defineProps<{
  cleaningData: boolean;
  quickCrawling: boolean;
}>();

// Emits
const emit = defineEmits<{
  (e: 'loadData'): void;
  (e: 'startQuickCrawl'): void;
  (e: 'openCrawlDialog'): void;
}>();

// 上传文件
const onUpload = async (options: any) => {
  try {
    const formData = new FormData();
    formData.append('file', options.file);
    await http.post('/data/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    });
    ElMessage.success('导入成功');
    emit('loadData');
  } catch (error) {
    ElMessage.error('导入失败');
  }
};

// 处理文件选择
const handleFileChange = (file: any) => {
  onUpload({ file: file.raw });
};

// ================= 截图视觉识别导入 =================
const visionDialogVisible = ref(false);
const visionLoading = ref(false);
const visionSaving = ref(false);
const visionResult = ref<any>(null);
const visionFile = ref<File | null>(null);

const beforeVisionUpload = (file: File) => {
  if (!/image\/(png|jpe?g|webp)/i.test(file.type)) {
    ElMessage.warning('仅支持 PNG / JPG / WEBP 图片');
    return false;
  }
  visionFile.value = file;
  doVisionRecognize(file);
  return false; // 阻止默认上传，改走自定义请求
};

const doVisionRecognize = async (file: File) => {
  visionLoading.value = true;
  visionResult.value = null;
  try {
    const formData = new FormData();
    formData.append('file', file);
    const res: any = await http.post('/jobs/vision-import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    });
    visionResult.value = res.result;
  } catch (e: any) {
    ElMessage.error(e?.message || '图片识别失败，请检查 ZHIPUAI_API_KEY 是否配置');
  } finally {
    visionLoading.value = false;
  }
};

const urlDialogVisible = ref(false);
const urlInput = ref('');
const urlLoading = ref(false);
const urlSaving = ref(false);
const urlResult = ref<any>(null);

const doUrlImport = async (validate: boolean) => {
  const url = urlInput.value.trim();
  if (!url) return;
  if (validate) {
    urlSaving.value = true;
  } else {
    urlLoading.value = true;
    urlResult.value = null;
  }
  try {
    const res: any = await http.post('/jobs/url-import', {
      url,
      validate
    });
    if (validate) {
      ElMessage.success(res.saved ? '已入库（后续将定期核查该岗位）' : '岗位已存在');
      urlDialogVisible.value = false;
      urlResult.value = null;
      urlInput.value = '';
      emit('loadData');
    } else {
      urlResult.value = res.result;
    }
  } catch (e: any) {
    ElMessage.error(e?.message || (validate ? '入库失败' : '识别失败'));
  } finally {
    urlLoading.value = false;
    urlSaving.value = false;
  }
};

const confirmVisionSave = async () => {
  if (!visionFile.value) return;
  visionSaving.value = true;
  try {
    const formData = new FormData();
    formData.append('file', visionFile.value);
    const res: any = await http.post('/jobs/vision-import?validate=true', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    });
    ElMessage.success(res.saved ? '已入库' : '岗位已存在');
    visionDialogVisible.value = false;
    visionResult.value = null;
    visionFile.value = null;
    emit('loadData');
  } catch (e: any) {
    ElMessage.error(e?.message || '入库失败');
  } finally {
    visionSaving.value = false;
  }
};


// 导出数据
const onExport = async () => {
  try {
    const res = await http.get('/data/export', { responseType: 'blob' } as any) as Blob;
    // 拦截器对 blob 请求直接返回 Blob 对象，无需再取 .data
    const blobData = res instanceof Blob ? res : new Blob([res], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const url = URL.createObjectURL(blobData);
    const a = document.createElement('a');
    a.href = url;
    a.download = '招聘数据.xlsx';
    a.click();
    URL.revokeObjectURL(url);
    ElMessage.success('导出成功');
  } catch (error) {
    ElMessage.error('导出失败');
  }
};

// 清洗数据库
const cleanupDatabase = async () => {
  try {
    await ElMessageBox.confirm(
      '该操作会清空岗位数据和爬虫任务记录，是否继续？',
      '清洗数据库确认',
      {
        confirmButtonText: '确定清洗',
        cancelButtonText: '取消',
        type: 'warning'
      }
    );

    const deletedCount = await http.post('/data/cleanup');
    ElMessage.success(`清洗完成，已删除 ${deletedCount || 0} 条岗位数据`);
    emit('loadData');
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error('数据库清洗失败');
    }
  }
};

// 一键爬取按钮点击
const handleQuickCrawl = () => {
  emit('startQuickCrawl');
};

// 打开创建爬虫任务对话框
const handleOpenCrawlDialog = () => {
  showCrawlDialog.value = true;
};

// 刷新数据按钮点击
const handleRefreshData = () => {
  emit('loadData');
};

// 暴露给父组件
const showCrawlDialog = defineModel<boolean>('showCrawlDialog', { default: false });
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header .title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #333;
}

.action-buttons {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}
</style>
