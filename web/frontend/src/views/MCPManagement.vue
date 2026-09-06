<template>
  <div class="mcp-management-container">
    <div class="content-header">
      <div class="content-title">🔧 MCP 管理</div>
    </div>
    <div class="content-body">
      <div class="coming-soon">
        <div class="coming-soon-icon">🔧</div>
        <div class="coming-soon-title">敬请期待</div>
        <div class="coming-soon-desc">MCP 管理功能正在开发中...</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
</script>

<style scoped>
.mcp-management-container {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.content-header {
  background: white;
  padding: 15px 25px;
  border-bottom: 1px solid #e0e0e0;
  flex-shrink: 0;
}

.content-title {
  font-size: 18px;
  font-weight: bold;
  color: #333;
}

.content-body {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.coming-soon {
  text-align: center;
  padding: 40px;
}

.coming-soon-icon {
  font-size: 64px;
  margin-bottom: 20px;
}

.coming-soon-title {
  font-size: 24px;
  font-weight: bold;
  color: #333;
  margin-bottom: 10px;
}

.coming-soon-desc {
  font-size: 14px;
  color: #999;
}
</style>
