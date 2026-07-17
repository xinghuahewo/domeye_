<template>
  <div class="system-menu-container layout-pd">
    <el-card shadow="hover">
      <!-- 查询条件区域 -->
      <div class="system-menu-search mb15" style="display: flex; position: relative">
        <el-date-picker
          v-model="state.timeRange"
          type="datetimerange"
          range-separator="To"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          size="default"
          style="max-width: 380px"
        />
        <el-input 
          size="default" 
          v-model="state.asNumber" 
          placeholder="AS号" 
          style="max-width: 120px" 
          class="ml10"
        />
        <!-- <el-input 
          size="default" 
          v-model="state.asName" 
          placeholder="AS名称" 
          style="max-width: 150px" 
          class="ml10"
        />
        <el-input 
          size="default" 
          v-model="state.orgName" 
          placeholder="组织名称" 
          style="max-width: 150px" 
          class="ml10"
        /> -->
        <el-input 
          size="default" 
          v-model="state.country" 
          placeholder="国家" 
          style="max-width: 120px" 
          class="ml10"
        />
        <el-button size="default" type="primary" class="ml15" @click="search">
          查询
        </el-button>
        <el-button size="default" class="ml15" @click="resetData">
          重置
        </el-button>
      </div>

      <!-- 表格区域 -->
      <el-table
        :data="state.data"
        v-loading="state.loading"
        style="width: 100%"
        size="default"
        border
        :table-layout="'auto'"
        :row-style="{ height: '120px' }"
        :cell-style="{ padding: '10px' }"
      >
        <el-table-column label="序号" width="60" fixed>
          <template #default="{ $index }">
            {{ (state.currentPage - 1) * state.pageSize + $index + 1 }}
          </template>
        </el-table-column>
        <el-table-column prop="as_number" label="AS号" width="100" />
        <el-table-column prop="as_name" label="AS名称" width="160" />
        <el-table-column prop="org_name" label="组织名称" width="200" />
        <el-table-column prop="country" label="国家" width="120" />
        <el-table-column label="微型时序图">
          <template #default="scope">
            <div style="height: 100px; width: 100%;">
              <MiniTimeSeriesChart 
                :data="scope.row.time_series_data" 
                width="100%"
                height="100px"
                :show-dual-lines="true"
              />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="scope">
            <el-button type="primary" link @click="toDetail(scope.row)">
              详情
            </el-button>
            <el-button type="primary" link @click="exportData(scope.row)">
              导出图片
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="table-footer mt20">
        <el-pagination
          v-model:current-page="state.currentPage"
          v-model:page-size="state.pageSize"
          :total="state.total"
          :page-sizes="[5, 10, 20]"
          background
          layout="prev, pager, next, jumper, sizes, ->, total"
          @current-change="handleCurrentChange"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import MiniTimeSeriesChart from '/@/components/feature/MiniTimeSeriesChart.vue';
import request from '/@/utils/request';
import baseUrl from '/@/api';
import * as echarts from 'echarts';

const router = useRouter();

const state = reactive({
  data: [] as any[],
  loading: false,
  timeRange: ['', ''] as string[],
  asNumber: '',
  asName: '',
  orgName: '',
  country: '',
  currentPage: 1,
  pageSize: 5,
  total: 0
});

// 查询数据
const search = async () => {
  state.loading = true;
  try {
    const params: any = {
      start_time: formatDateTime(new Date(state.timeRange[0])),
      end_time: formatDateTime(new Date(state.timeRange[1])),
      page_num: state.currentPage,
      page_size: state.pageSize
    };

    // 只有在有值的时候才添加到参数中
    if (state.asNumber) params.asn = state.asNumber;
    if (state.country) params.country = state.country;

    const response = await request({
      url: `${baseUrl}features/ases`,
      method: 'get',
      params
    });

    if (response && Array.isArray(response.data)) {
      // 转换数据格式以适配表格显示
      state.data = response.data.map((item: any) => ({
        as_number: item.asn,
        as_name: item.as_name,
        org_name: item.org_name,
        country: item.country,
        time_series_data: item.time_series_data
      }));
      state.total = response.record_count;
      // ElMessage.success(`查询成功，共找到${response.record_count}个AS`);
    }
  } catch (error) {
    console.error('查询失败:', error);
    ElMessage.error('查询失败，请检查网络连接');
    state.data = [];
    state.total = 0;
  } finally {
    state.loading = false;
  }
};

// 重置查询条件
const resetData = () => {
  // 重置时间范围为最近1天
  const now = new Date();
  const oneDayAgo = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  state.timeRange = [
    formatDateTime(oneDayAgo),
    formatDateTime(now)
  ];
  state.asNumber = '';
  state.asName = '';
  state.orgName = '';
  state.country = '';
  state.currentPage = 1;
  search();
};

// 跳转详情页
const toDetail = (row: any) => {
  const routeData = router.resolve({
    name: 'asFeatureDetail',
    query: {
      asn: row.as_number,
      start_time: state.timeRange[0],
      end_time: state.timeRange[1]
    }
  });
  window.open(routeData.href, '_blank');
};

// 导出图片
const exportData = (row: any) => {
  // 创建一个虚拟的图表来生成图片
  if (!row.time_series_data || row.time_series_data.length === 0) {
    ElMessage.warning('暂无数据可导出');
    return;
  }

  // 创建虚拟canvas
  const canvas = document.createElement('canvas');
  canvas.width = 800;
  canvas.height = 400;
  const tempChart = echarts.init(canvas);

  const timeData = row.time_series_data.map((item: any) => item.time);
  const withdrawData = row.time_series_data.map((item: any) => item.withdraw);
  const announceData = row.time_series_data.map((item: any) => item.announce);

  const option = {
    title: {
      text: `AS${row.as_number} 时序特征`,
      left: 'center',
      textStyle: {
        fontSize: 16,
        fontWeight: 'bold'
      }
    },
    tooltip: {
      trigger: 'axis'
    },
    legend: {
      data: ['回撤报文', '宣告报文'],
      top: 'bottom'
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '15%',
      top: '15%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: timeData,
      axisLabel: {
        rotate: 45
      }
    },
    yAxis: {
      type: 'value',
      name: '报文数量'
    },
    series: [
      {
        name: '回撤报文',
        type: 'line',
        data: withdrawData,
        smooth: true,
        lineStyle: { color: '#1f77b4' },
        itemStyle: { color: '#1f77b4' }
      },
      {
        name: '宣告报文',
        type: 'line',
        data: announceData,
        smooth: true,
        lineStyle: { color: '#d62728' },
        itemStyle: { color: '#d62728' }
      }
    ]
  };

  tempChart.setOption(option);

  // 获取图片数据
  setTimeout(() => {
    try {
      const dataURL = tempChart.getDataURL({
        type: 'png',
        pixelRatio: 2,
        backgroundColor: '#fff'
      });

      const link = document.createElement('a');
      link.download = `AS${row.as_number}_时序特征图.png`;
      link.href = dataURL;
      link.click();
      
      ElMessage.success('图片导出成功');
      tempChart.dispose();
    } catch (error) {
      console.error('导出失败:', error);
      ElMessage.error('导出失败');
      tempChart.dispose();
    }
  }, 100);
};

// 分页处理
const handleCurrentChange = (page: number) => {
  state.currentPage = page;
  search();
};

const handleSizeChange = (size: number) => {
  state.pageSize = size;
  state.currentPage = 1;
  search();
};

// 格式化日期时间
const formatDateTime = (date: Date): string => {
  const Y = date.getFullYear();
  const M = (date.getMonth() + 1).toString().padStart(2, '0');
  const D = date.getDate().toString().padStart(2, '0');
  const h = date.getHours().toString().padStart(2, '0');
  const m = date.getMinutes().toString().padStart(2, '0');
  const s = date.getSeconds().toString().padStart(2, '0');
  return `${Y}-${M}-${D} ${h}:${m}:${s}`;
};

onMounted(() => {
  // 设置默认时间范围（最近1天）
  const now = new Date();
  const oneDayAgo = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  state.timeRange = [
    formatDateTime(oneDayAgo),
    formatDateTime(now)
  ];
  search();
});
</script>