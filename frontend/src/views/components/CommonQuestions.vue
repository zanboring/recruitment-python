<template>
  <div class="common-questions">
    <h3 class="questions-title">
      <el-icon class="questions-icon"><ChatLineSquare /></el-icon>常用问题
    </h3>
    <div class="questions-grid">
      <el-tag v-for="(question, index) in questions" :key="question"
        class="question-tag" @click="$emit('select', question)"
        effect="plain" :class="{ 'tag-hover': hoveredIndex === index }"
        @mouseenter="hoveredIndex = index" @mouseleave="hoveredIndex = -1">
        <el-icon class="tag-icon"><ChatDotRound /></el-icon>{{ question }}
      </el-tag>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ChatDotRound, ChatLineSquare } from '@element-plus/icons-vue';

defineProps<{ questions: string[] }>();
defineEmits<{ select: [question: string] }>();

const hoveredIndex = ref(-1);
</script>

<style scoped>
.common-questions { padding: 0 20px 20px; }
.questions-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 14px; font-weight: bold; margin-bottom: 16px;
  color: var(--text-regular); }
.questions-icon { color: var(--info-color); }

.questions-grid { display: flex; flex-wrap: wrap; gap: 10px; }
.question-tag {
  display: flex; align-items: center; gap: 6px;
  padding: 10px 18px; border-radius: 24px; cursor: pointer;
  transition: all .3s cubic-bezier(.4,0,.2,1);
  border: 1px solid var(--border-color); background: #fff; color: var(--text-regular);
  position: relative; overflow: hidden;

  &:hover { transform: translateY(-3px); box-shadow: 0 6px 18px rgba(64,158,255,.15); border-color: var(--info-color); }
}
.tag-icon { font-size: 14px; color: var(--primary-color); }
</style>
