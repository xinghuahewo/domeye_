<template>
  <div class="AllContainer">
    <div class="ItemContainer" v-for="(item, index) in props.data" :key="index">
      <el-row class="Item" justify="space-around" align="middle">
        <el-col :span="8" class="IconContainer">
          <img :src="IconPath[index]" style="width: 100%;margin: 10px">
        </el-col>
        <el-col :span="15">
          <div style="width: 100%;height: 20px;font-size: 10px;line-height: 20px;text-align: center;color: white">
            <span>{{ item.title }}</span>
          </div>
          <div style="width: 100%;height: 32px;font-size: 30px;line-height: 32px;text-align: center;font-weight: bolder;color: yellow">
            {{ item.num }}
          </div>
          <div style="width: 100%;height: 20px;font-size: 10px;line-height: 20px;text-align: center;color: white">
            <span :style="{color: item.changeType ? '#1ad45b' : '#d41a1a'}">
              {{ item.changeNum }}
              <el-icon :color="item.changeType ? '#1ad45b' : '#d41a1a'">
                <CaretTop v-if="item.changeType" />
                <CaretBottom v-else />
              </el-icon>
            </span>
          </div>
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<script setup lang="ts" name="LeftTop">
import {CaretTop, CaretBottom} from '@element-plus/icons-vue'
import Icon1 from '/@/assets/icons/inventoryIcon-库存.png'
import Icon2 from '/@/assets/icons/blogIcon-博客.png'
import Icon3 from '/@/assets/icons/connectorIcon-亚马逊连接器.png'
import Icon4 from '/@/assets/icons/accountingIcon-会计.png'

const props = defineProps({
  data: Array
})

const IconPath = [
  Icon1,
  Icon2,
  Icon3,
  Icon4,
]
</script>

<style scoped lang="scss">
.AllContainer{
  width: 100%;
  height: 100%;

  .ItemContainer {
    width: 50%;
    height: 50%;
    padding: 1%;
    float: left;

    .Item {
      width: 100%;
      height: 100%;
    }
  }
}
</style>