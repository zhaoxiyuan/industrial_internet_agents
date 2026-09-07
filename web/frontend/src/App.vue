<template>
  <div class="layout">
    <!-- 顶部框架栏 -->
    <div class="framework-header">
      <div class="header-logo">
        <div class="header-logo-icon">🤖</div>
        <div class="header-title">边缘多智能体管理平台</div>
      </div>
      <div class="header-right">
        <div class="header-status">
          <span class="status-dot"></span>
          <span>运行中</span>
        </div>
      </div>
    </div>

    <!-- 主体区域 -->
    <div class="main-container">
      <!-- 侧边栏 -->
      <div class="sidebar">
        <div class="sidebar-section">
          <router-link to="/agents" :class="['sidebar-item', { active: route.path.startsWith('/agents') }]">
            <span class="sidebar-icon">🤖</span>
            <span>Agent 管理</span>
          </router-link>
          <div class="submenu-group">
            <div class="submenu-parent" @click="toggleSceneMenu">
              <router-link to="/edge-platform" :class="['sidebar-item', { active: route.path === '/edge-platform' && !route.query.agent }]" @click.stop>
                <span class="submenu-icon">🚀</span>
                <span>广东石化作业场景</span>
              </router-link>
              <span class="submenu-arrow">{{ sceneMenuOpen ? '▼' : '▶' }}</span>
            </div>
            <div v-show="sceneMenuOpen" class="submenu" style="padding-left: 15px;">
              <router-link
                v-for="agent in subAgents"
                :key="agent.id"
                :to="`/edge-platform?agent=${agent.id}`"
                :class="['sidebar-item submenu-item', { active: route.query.agent === agent.id }]"
              >
                <span class="submenu-icon">{{ agent.icon }}</span>
                <span>{{ agent.name }}</span>
              </router-link>
            </div>
          </div>
          <router-link to="/skills" :class="['sidebar-item', { active: route.path === '/skills' }]">
            <span class="sidebar-icon">🛠️</span>
            <span>Skill 广场</span>
          </router-link>
          <router-link to="/workflow" :class="['sidebar-item', { active: route.path.startsWith('/workflow') }]">
            <span class="sidebar-icon">📊</span>
            <span>工作流</span>
          </router-link>
          <router-link to="/model-management" :class="['sidebar-item', { active: route.path === '/model-management' }]">
            <span class="sidebar-icon">📦</span>
            <span>模型管理</span>
          </router-link>
          <router-link to="/mcp-management" :class="['sidebar-item', { active: route.path === '/mcp-management' }]">
            <span class="sidebar-icon">🔧</span>
            <span>MCP 管理</span>
          </router-link>
        </div>
      </div>

      <!-- 主内容区 -->
      <div class="main-content">
        <router-view />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const sceneMenuOpen = ref(false)
const subAgents = [
  { id: 'MAIN', name: '主调度 Agent', icon: '🎛️' },
  { id: 'P1', name: 'P1 作业票 Agent', icon: '📋' },
  { id: 'P2', name: 'P2 任务获取 Agent', icon: '📥' },
  { id: 'P3', name: 'P3 上下文 Agent', icon: '🧠' },
  { id: 'P4', name: 'P4 数据关联 Agent', icon: '📹' },
  { id: 'P5', name: 'P5 条件核验 Agent', icon: '✅' },
  { id: 'P6', name: 'P6 过程监测 Agent', icon: '👁️' },
  { id: 'P7', name: 'P7 风险研判 Agent', icon: '⚠️' },
  { id: 'P8', name: 'P8 人机处置 Agent', icon: '🔔' },
  { id: 'P9', name: 'P9 闭环跟踪 Agent', icon: '🔄' },
  { id: 'P10', name: 'P10 归档复盘 Agent', icon: '📦' },
]

function toggleSceneMenu() {
  sceneMenuOpen.value = !sceneMenuOpen.value
}
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

.layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: #f0f2f5;
  color: #333;
}

.framework-header {
  height: 60px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  display: flex;
  align-items: center;
  padding: 0 20px;
  box-shadow: 0 2px 10px rgba(0,0,0,0.15);
  flex-shrink: 0;
}

.header-logo {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-logo-icon {
  width: 36px;
  height: 36px;
  background: rgba(255,255,255,0.2);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}

.header-title {
  font-size: 18px;
  font-weight: bold;
  letter-spacing: 1px;
}

.header-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 15px;
}

.header-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.status-dot {
  width: 8px;
  height: 8px;
  background: #4CAF50;
  border-radius: 50%;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.main-container {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.sidebar {
  width: 200px;
  background: linear-gradient(180deg, #667eea 0%, #764ba2 100%);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.sidebar-section {
  padding: 20px 0;
}

.sidebar-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  cursor: pointer;
  transition: all 0.2s;
  font-size: 14px;
  color: rgba(255,255,255,0.8);
  border-left: 3px solid transparent;
  text-decoration: none;
}

.sidebar-item:hover {
  background: rgba(255,255,255,0.1);
  color: white;
}

.sidebar-item.active {
  background: rgba(255,255,255,0.2);
  color: white;
  border-left-color: white;
  font-weight: 500;
}

.sidebar-icon {
  font-size: 16px;
}

.sidebar-badge {
  margin-left: auto;
  background: rgba(255,255,255,0.2);
  color: white;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: bold;
}

.submenu {
  padding-left: 15px;
}

.submenu-group {
  display: flex;
  flex-direction: column;
}

.submenu-parent {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  cursor: pointer;
  transition: all 0.2s;
  font-size: 14px;
  color: rgba(255,255,255,0.8);
  border-left: 3px solid transparent;
}

.submenu-parent:hover {
  background: rgba(255,255,255,0.1);
  color: white;
}

.submenu-arrow {
  margin-left: auto;
  font-size: 10px;
  opacity: 0.7;
}

.submenu-item {
  padding-left: 35px !important;
  font-size: 13px !important;
}

.submenu-icon {
  font-size: 14px;
}

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #f0f2f5;
}
</style>
