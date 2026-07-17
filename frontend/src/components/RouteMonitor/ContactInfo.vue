<template>
  <div class="ContactInfoContainer">
    <el-collapse v-model="activeNames">
      <el-collapse-item title="管理联系人" name="1">
        <el-table :data="tableDatas.adminData" style="margin: 0 10px;width: calc(100% - 20px)">
          <el-table-column prop="name" label="名称" min-width="90"/>
          <el-table-column prop="descr" label="描述" min-width="130" />
          <el-table-column prop="address" label="地址" min-width="130" />
          <el-table-column prop="tel" label="电话" min-width="130" />
          <el-table-column prop="fax" label="传真" min-width="130" />
          <el-table-column prop="email" label="邮件" min-width="130" />
        </el-table>
      </el-collapse-item>
      <el-collapse-item title="技术联系人" name="2">
        <el-table :data="tableDatas.techData" style="margin: 0 10px;width: calc(100% - 20px)">
          <el-table-column prop="name" label="名称" min-width="90"/>
          <el-table-column prop="descr" label="描述" min-width="130" />
          <el-table-column prop="address" label="地址" min-width="130" />
          <el-table-column prop="tel" label="电话" min-width="130" />
          <el-table-column prop="fax" label="传真" min-width="130" />
          <el-table-column prop="email" label="邮件" min-width="130" />
        </el-table>
      </el-collapse-item>
      <el-collapse-item title="滥用联系人" name="3">
        <el-table :data="tableDatas.abuseData" style="margin: 0 10px;width: calc(100% - 20px)">
          <el-table-column prop="name" label="名称" min-width="90"/>
          <el-table-column prop="descr" label="描述" min-width="130" />
          <el-table-column prop="address" label="地址" min-width="130" />
          <el-table-column prop="tel" label="电话" min-width="130" />
          <el-table-column prop="fax" label="传真" min-width="130" />
          <el-table-column prop="email" label="邮件" min-width="130" />
        </el-table>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script>
export default {
  name: 'EventReplay',
  props: ['subdata', 'type'],	// subdata为整个事件,
  data(){
    return{
      activeNames: ['1', '2', '3'],
      tableDatas: {
        adminData: [],
        techData: [],
        abuseData: [],
      }
    }
  },
  created() {
    if(this.type === '边界中断'){
      this.tableDatas.adminData.push(this.subdata.export_admin[0])
      this.tableDatas.adminData.push(this.subdata.peer_admin[0])
      this.tableDatas.techData.push(this.subdata.export_tech[0])
      this.tableDatas.techData.push(this.subdata.peer_tech[0])
      this.tableDatas.abuseData.push(this.subdata.export_abuse[0])
      this.tableDatas.abuseData.push(this.subdata.peer_abuse[0])
    }
    else if(this.type === '前缀中断' || this.type === 'AS中断'){
      this.tableDatas.adminData.push(this.subdata.attacked_admin[0])
      this.tableDatas.techData.push(this.subdata.attacked_tech[0])
      this.tableDatas.abuseData.push(this.subdata.attacked_abuse[0])
    }
    else if(this.type !== 'RPKI证书异常'){
      this.tableDatas.adminData.push(this.subdata.attacked_admin[0])
      this.tableDatas.adminData.push(this.subdata.attacker_admin[0])
      this.tableDatas.techData.push(this.subdata.attacked_tech[0])
      this.tableDatas.techData.push(this.subdata.attacker_tech[0])
      this.tableDatas.abuseData.push(this.subdata.attacked_abuse[0])
      this.tableDatas.abuseData.push(this.subdata.attacker_abuse[0])
    }
  }
}

</script>

<style scoped lang="scss">
.ContactInfoContainer{
  width: calc(100% - 60px);
  margin: 0 30px;
}
</style>