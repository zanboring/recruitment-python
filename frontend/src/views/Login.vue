<template>
  <div class="login-container">
    <!-- 粒子背景画布 -->
    <canvas ref="particleCanvas" class="particle-canvas"></canvas>

    <div class="login-content">
      <div class="login-brand">
        <div class="brand-icon">
          <el-icon :size="60" color="#fff"><DataBoard /></el-icon>
        </div>
        <h1 class="brand-title">Recruitment</h1>
        <p class="brand-subtitle">招聘数据可视化系统</p>
      </div>

      <el-card class="login-card" shadow="hover">
        <div class="card-header">
          <h2>{{ isRegisterMode ? '创建账号' : '欢迎登录' }}</h2>
          <p>{{ isRegisterMode ? '填写信息完成注册' : 'Enter your credentials to access the system' }}</p>
        </div>

        <!-- 登录表单 -->
        <el-form v-if="!isRegisterMode" :model="form" :rules="rules" ref="formRef" label-position="top">
          <el-form-item label="用户名" prop="username">
            <el-input
              v-model="form.username"
              placeholder="请输入用户名"
              size="large"
              :prefix-icon="User"
            />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              size="large"
              show-password
              :prefix-icon="Lock"
            />
          </el-form-item>
          <el-form-item>
            <el-checkbox v-model="form.rememberMe">记住我</el-checkbox>
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              size="large"
              class="login-btn"
              :loading="loading"
              @click="onSubmit"
            >
              登录
            </el-button>
          </el-form-item>
          <el-form-item>
            <div class="register-link">
              还没有账号？<el-button type="primary" link @click="switchToRegister">立即注册</el-button>
            </div>
          </el-form-item>
        </el-form>

        <!-- 注册表单 -->
        <el-form v-else :model="registerForm" :rules="registerRules" ref="registerFormRef" label-position="top">
          <el-form-item label="用户名" prop="username">
            <el-input
              v-model="registerForm.username"
              placeholder="请输入用户名（用于登录）"
              size="large"
              :prefix-icon="User"
            />
          </el-form-item>
          <el-form-item label="邮箱" prop="email">
            <el-input
              v-model="registerForm.email"
              placeholder="请输入邮箱地址"
              size="large"
              :prefix-icon="Message"
            />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input
              v-model="registerForm.password"
              type="password"
              placeholder="密码长度6到32位"
              size="large"
              show-password
              :prefix-icon="Lock"
            />
          </el-form-item>
          <el-form-item label="确认密码" prop="confirmPassword">
            <el-input
              v-model="registerForm.confirmPassword"
              type="password"
              placeholder="请再次输入密码"
              size="large"
              show-password
              :prefix-icon="Lock"
            />
          </el-form-item>
          <el-form-item>
            <div class="register-actions">
              <el-button size="large" class="back-btn" @click="switchToLogin">
                返回登录
              </el-button>
              <el-button
                type="primary"
                size="large"
                class="login-btn"
                :loading="loading"
                @click="onRegister"
              >
                注册
              </el-button>
            </div>
          </el-form-item>
        </el-form>
      </el-card>

      <div class="login-footer">
        <p>&copy; 2024 Recruitment System. All rights reserved.</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted, onUnmounted } from 'vue';
import { useRouter } from 'vue-router';
import { loginApi, registerApi, defaultUsernameApi } from '@/api/auth';
import { useUserStore } from '@/store/user';
import { ElMessage, FormInstance, FormRules } from 'element-plus';
import { User, Lock, DataBoard, Message } from '@element-plus/icons-vue';

const router = useRouter();
const userStore = useUserStore();
const particleCanvas = ref<HTMLCanvasElement | null>(null);

// 是否处于注册模式
const isRegisterMode = ref(false);

// 登录表单
const form = reactive({
  username: '',
  password: '',
  rememberMe: false
});

// 注册表单
const registerForm = reactive({
  username: '',
  email: '',
  password: '',
  confirmPassword: ''
});

// 确认密码校验器
const validateConfirmPassword = (rule: any, value: string, callback: any) => {
  if (value !== registerForm.password) {
    callback(new Error('两次输入的密码不一致'));
  } else {
    callback();
  }
};

// 登录表单校验规则
const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度为3-20个字符', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' }
  ]
};

// 注册表单校验规则
const registerRules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度为3-20个字符', trigger: 'blur' }
  ],
  email: [
    { required: true, message: '请输入邮箱地址', trigger: 'blur' },
    { type: 'email', message: '请输入有效的邮箱地址', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 32, message: '密码长度为6-32位', trigger: 'blur' }
  ],
  confirmPassword: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    { validator: validateConfirmPassword, trigger: 'blur' }
  ]
};

const formRef = ref<FormInstance>();
const registerFormRef = ref<FormInstance>();
const loading = ref(false);

const onSubmit = () => {
  if (!formRef.value) return;
  formRef.value.validate(async (valid) => {
    if (!valid) return;
    loading.value = true;
    try {
      const user = await loginApi(form);
      userStore.setUser(user);
      
      if (form.rememberMe) {
        localStorage.setItem('rememberedUsername', form.username);
      } else {
        localStorage.removeItem('rememberedUsername');
      }
      
      ElMessage.success('登录成功');
      router.push('/');
    } finally {
      loading.value = false;
    }
  });
};

// 切换到注册模式
const switchToRegister = () => {
  isRegisterMode.value = true;
  // 预填用户名（如果登录表单已填写）
  if (form.username) {
    registerForm.username = form.username;
  }
};

// 切换回登录模式
const switchToLogin = () => {
  isRegisterMode.value = false;
  // 预填用户名（如果注册表单已填写）
  if (registerForm.username) {
    form.username = registerForm.username;
  }
};

// 注册处理
const onRegister = () => {
  if (!registerFormRef.value) return;
  registerFormRef.value.validate(async (valid) => {
    if (!valid) return;
    loading.value = true;
    try {
      await registerApi({
        username: registerForm.username,
        password: registerForm.password,
        email: registerForm.email
      });
      ElMessage.success('注册成功，请使用新账号登录');
      // 切换回登录模式并预填信息
      form.username = registerForm.username;
      isRegisterMode.value = false;
    } finally {
      loading.value = false;
    }
  });
};

class Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  color: string;

  constructor(canvasWidth: number, canvasHeight: number) {
    this.x = Math.random() * canvasWidth;
    this.y = Math.random() * canvasHeight;
    this.vx = (Math.random() - 0.5) * 2;
    this.vy = (Math.random() - 0.5) * 2;
    this.size = Math.random() * 3 + 1;
    const colors = ['#667eea', '#764ba2', '#f093fb', '#f5576c', '#4facfe', '#00f2fe', '#43e97b', '#38f9d7'];
    this.color = colors[Math.floor(Math.random() * colors.length)];
  }

  update(canvasWidth: number, canvasHeight: number) {
    this.x += this.vx;
    this.y += this.vy;

    if (this.x < 0 || this.x > canvasWidth) this.vx *= -1;
    if (this.y < 0 || this.y > canvasHeight) this.vy *= -1;
  }

  draw(ctx: CanvasRenderingContext2D) {
    ctx.beginPath();
    ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
    ctx.fillStyle = this.color;
    ctx.fill();
  }
}

let particles: Particle[] = [];
let animationId: number;

const initParticles = () => {
  const canvas = particleCanvas.value;
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;

  particles = [];
  const particleCount = Math.floor((canvas.width * canvas.height) / 15000);
  for (let i = 0; i < particleCount; i++) {
    particles.push(new Particle(canvas.width, canvas.height));
  }
};

const animate = () => {
  const canvas = particleCanvas.value;
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  for (let i = 0; i < particles.length; i++) {
    for (let j = i + 1; j < particles.length; j++) {
      const dx = particles[i].x - particles[j].x;
      const dy = particles[i].y - particles[j].y;
      const distance = Math.sqrt(dx * dx + dy * dy);

      if (distance < 120) {
        ctx.beginPath();
        ctx.strokeStyle = `rgba(102, 126, 234, ${0.3 - distance / 400})`;
        ctx.lineWidth = 0.5;
        ctx.moveTo(particles[i].x, particles[i].y);
        ctx.lineTo(particles[j].x, particles[j].y);
        ctx.stroke();
      }
    }
  }

  particles.forEach(p => {
    p.update(canvas.width, canvas.height);
    p.draw(ctx);
  });

  animationId = requestAnimationFrame(animate);
};

const handleResize = () => {
  const canvas = particleCanvas.value;
  if (!canvas) return;
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  initParticles();
};

onMounted(async () => {
  // 从后端获取默认用户名自动填充（不暴露密码）
  try {
    const res = await defaultUsernameApi();
    if (res?.username) {
      form.username = res.username;
      form.rememberMe = true;
    }
  } catch (_) {
    // 获取失败时回退到 localStorage 中的记住用户名
    const rememberedUsername = localStorage.getItem('rememberedUsername');
    if (rememberedUsername) {
      form.username = rememberedUsername;
      form.rememberMe = true;
    }
  }

  initParticles();
  animate();
  window.addEventListener('resize', handleResize);
});

onUnmounted(() => {
  cancelAnimationFrame(animationId);
  window.removeEventListener('resize', handleResize);
});
</script>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
  position: relative;
  overflow: hidden;
}

.particle-canvas {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 1;
}

.login-content {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px;
}

.login-brand {
  text-align: center;
  margin-bottom: 40px;
}

.brand-icon {
  width: 100px;
  height: 100px;
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.8), rgba(118, 75, 162, 0.8));
  border-radius: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 20px;
  box-shadow: 0 20px 60px rgba(102, 126, 234, 0.4);
  animation: pulse 3s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.05); }
}

.brand-title {
  font-size: 36px;
  font-weight: 700;
  color: #fff;
  margin: 0;
  letter-spacing: 4px;
  text-shadow: 0 4px 20px rgba(102, 126, 234, 0.5);
}

.brand-subtitle {
  font-size: 16px;
  color: rgba(255, 255, 255, 0.7);
  margin: 8px 0 0;
  letter-spacing: 2px;
}

.login-card {
  width: 420px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.2);
  box-shadow: 0 25px 50px rgba(0, 0, 0, 0.3);
}

.login-card :deep(.el-card__body) {
  padding: 40px;
}

.card-header {
  text-align: center;
  margin-bottom: 32px;
}

.card-header h2 {
  font-size: 28px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 8px;
}

.card-header p {
  font-size: 14px;
  color: #909399;
  margin: 0;
}

.login-btn {
  width: 100%;
  height: 48px;
  font-size: 16px;
  font-weight: 600;
  border-radius: 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  transition: all 0.3s ease;
}

.login-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 30px rgba(102, 126, 234, 0.4);
}

.register-link {
  text-align: center;
  color: #909399;
  font-size: 14px;
}

.register-actions {
  display: flex;
  gap: 12px;
  width: 100%;
}

.register-actions .back-btn {
  flex: 1;
  border-radius: 12px;
  font-weight: 600;
}

.register-actions .login-btn {
  flex: 2;
}

.login-footer {
  margin-top: 30px;
  text-align: center;
}

.login-footer p {
  color: rgba(255, 255, 255, 0.5);
  font-size: 12px;
  margin: 0;
}
</style>