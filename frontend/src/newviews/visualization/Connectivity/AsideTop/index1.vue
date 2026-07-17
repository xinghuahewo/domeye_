<template>
  <div class="AllContainer">
    <div class="ItemContainer" v-for="(item, index) in props.data" :key="index">
      <div class="Item">
        <div class="TopTitleContainer">
          {{ item.title }}
        </div>
        <div class="CenterTitleContainer">{{ item.num }}</div>
        <div class="BottomTitleContainer">
          <span>同期变幅：</span>
          <span :style="{color: item.changeType ? '#1ad45b' : '#d41a1a'}">
            {{ item.changeNum }}
            <el-icon :color="item.changeType ? '#1ad45b' : '#d41a1a'">
              <Top v-if="item.changeType" />
              <Bottom v-else />
            </el-icon>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts" name="LeftTop">
import {Top, Bottom} from '@element-plus/icons-vue'

const props = defineProps({
  data: Array
})

</script>

<style scoped lang="scss">
.AllContainer {
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

      .TopTitleContainer{
        width: 100%;
        height: 24px;
        font-size: 14px;
        line-height: 24px;
        text-align: left;
        font-weight: bolder;
        color: white;
      }

      .CenterTitleContainer{
        width: 100%;
        font-size: xx-large;
        text-align: center;
        font-weight: bolder;
        color: #00e6ff;
        font-family: Bahnschrift,serif;
        margin-top: 10px;
        margin-bottom: 10px;
      }

      .BottomTitleContainer{
        width: 100%;
        height: 20px;
        font-size: 14px;
        line-height: 20px;
        text-align: center;
        font-weight: bolder;
        color: white;
      }
    }
  }
}
</style>