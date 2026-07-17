<template>
    <div>
      <!-- Template button that opens the dialog -->
      <el-button type="primary" link @click="openDialog" class="ml10">模板</el-button>
  
      <!-- Template selection dialog -->
      <el-dialog
        title="模板类型"
        v-model="dialogVisible"
        width="400px"
        destroy-on-close
        :modal="true"
        :append-to-body="true"
        custom-class="template-dialog"
        >
        <div class="template-options">
          <el-checkbox 
            v-for="option in templateOptions" 
            :key="option.id" 
            v-model="option.selected"
            class="template-option"
          >
            {{ option.label }}
          </el-checkbox>
        </div>
        
        <template #footer>
          <span class="dialog-footer">
            <el-button @click="dialogVisible = false">取消</el-button>
            <el-button 
              type="primary" 
              @click="generateTemplates" 
              :disabled="!hasSelectedTemplates"
            >
            下载模板
            </el-button>
          </span>
        </template>
      </el-dialog>
    </div>
  </template>
  
  <script lang="ts" setup>
  import { ref, computed } from 'vue';
  import { ElMessage } from 'element-plus';
  import request from "/@/utils/request";
  import baseUrl from "/@/api";
  import axios from 'axios';

  // Props
  interface Props {
    detailUrl: string
  }
  const props = defineProps<Props>()
  
  // Template options
  interface TemplateOption {
    id: string
    label: string
    selected: boolean
  }
  
  const templateOptions = ref<TemplateOption[]>([
    { id: 'type1', label: '经典报告模板', selected: false },
    { id: 'type2', label: '通报时间模板（表格）', selected: false },
    { id: 'type3', label: '事件报告模板（标题）', selected: false }
  ])
  
  const dialogVisible = ref(false)
  
  // Computed property to check if any template is selected
  const hasSelectedTemplates = computed(() => {
    return templateOptions.value.some(option => option.selected)
  })
  
  // Open dialog and reset selections
  const openDialog = () => {
    dialogVisible.value = true
    templateOptions.value.forEach(option => {
      option.selected = false
    })
  }
  
  // Generate templates for each selected option
  const generateTemplates = async () => {
    const selectedTypes = templateOptions.value
      .filter(option => option.selected)
      .map(option => option.id)
    
    // Close the dialog
    dialogVisible.value = false
    
    // Generate each selected template in a separate window
    for (const templateType of selectedTypes) {
      try {
        console.log(templateType)
        const download_url = await request({
        // url: 'http://10.3.242.226:19746/template-export',
            url: baseUrl + 'reports/template-export',
            method: 'post',
            data: {
                detail_url: props.detailUrl,
                templateType: templateType,
            },
        });
        ElMessage({
            message: download_url.split("-")[5] + '模板已生成',
            type: 'success',
        })
        
        const url = baseUrl + 'reports/download/' + download_url;
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      } catch (error) {
        ElMessage.error('请求错误，无法连接到服务器')
      }
    }
  }
  </script>
  
  <style scoped>
  .template-dialog {
  z-index: 2000 !important; /* ElementUI默认dialog的z-index是2000，确保更高 */
  }
  .template-options {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  
  .template-option {
    margin-bottom: 0;
  }
  
  .dialog-footer {
    display: flex;
    justify-content: flex-end;
    gap: 12px;
  }
  .table-container {
  position: relative;
  z-index: 1; /* 确保低于dialog的z-index */
 }
  </style>