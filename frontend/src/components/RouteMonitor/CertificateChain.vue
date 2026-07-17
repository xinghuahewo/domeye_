<template>
  <div class="CertificateChainContainer" id="chart">
  </div>
</template>

<script>
import * as echarts from "echarts"

export default {
  name: "CertificateChain",
  props: ['subdata', 'type'],
  data(){
    return{

    }
  },
  watch: {
    subdata() {
      this.drawChart()
    }
  },
  methods: {
    drawChart(){
      let chartDom = document.getElementById('chart');
      let myChart = echarts.init(chartDom);

      // 设置数据
      let chartData = []

      for (let i = 1; i <= this.subdata.chainCerts.parentChainCers.length; i++) {
        let temp = {
          value: 100,
          name: this.subdata.chainCerts.parentChainCers[this.subdata.chainCerts.parentChainCers.length - i].jsonAll.subject,
          c: "名称： " + this.subdata.chainCerts.parentChainCers[this.subdata.chainCerts.parentChainCers.length - i].jsonAll.subject
              + ";\nValidity:  " + this.subdata.chainCerts.parentChainCers[this.subdata.chainCerts.parentChainCers.length - i].jsonAll.notBefore + ' - ' + this.subdata.chainCerts.parentChainCers[this.subdata.chainCerts.parentChainCers.length - i].jsonAll.notAfter
              + ";\nASNs:  " + this.subdata.chainCerts.parentChainCers[this.subdata.chainCerts.parentChainCers.length - i].jsonAll.asnModel.asns.map((item) => item.asn).join(','),
          itemStyle: {
            color: '#1ad45b',
          },
          }
        chartData.push(temp)
      }

      chartData.push({
        value: 100,
        name: this.subdata.jsonAll.subject,
        c: "名称：" + this.subdata.jsonAll.subject
            + ";\nValidity:  " + this.subdata.jsonAll.notBefore + ' - ' + this.subdata.jsonAll.notAfter
            + ";\nASNs:  " + this.subdata.jsonAll.asnModel.asns.map((item) => item.asn).join(','),
        itemStyle: {
          color: '#d41a1a',
        },
      })

      if(this.subdata.chainCerts.hasOwnProperty('childChainCers')){
        for (let i = 0; i < this.subdata.chainCerts.childChainCers.length; i++) {
          let temp = {
            value: 100,
            name: this.subdata.chainCerts.childChainCers[i].jsonAll.subject,
            c: "名称：" + this.subdata.chainCerts.childChainCers[i].jsonAll.subject
                + ";\nValidity:  " + this.subdata.chainCerts.childChainCers[i].jsonAll.notBefore + ' - ' + this.subdata.chainCerts.childChainCers[i].jsonAll.notAfter
                + ";\nASNs:  " + this.subdata.chainCerts.childChainCers[i].jsonAll.asnModel.asns.map((item) => item.asn).join(','),
            itemStyle: {
              color: '#d4611a',
            },
          }
          chartData.push(temp)
        }
      }

      let option = {
        tooltip: {
          trigger: 'item',
          formatter: function(params){
            return params.data.c
          },
          extraCssText:'white-space:pre-wrap'
        },
        series: [
          {
            name: 'Funnel',
            type: 'funnel',
            left: '10%',
            top: 60,
            bottom: 60,
            width: '80%',
            min: 0,
            max: 100,
            minSize: '100%',
            maxSize: '100%',
            sort: 'descending',
            gap: 2,
            label: {
              show: true,
              position: 'inside'
            },
            labelLine: {
              length: 10,
              lineStyle: {
                width: 1,
                type: 'solid'
              }
            },
            itemStyle: {
              borderColor: '#fff',
              borderWidth: 1
            },
            emphasis: {
              label: {
                fontSize: 20
              }
            },
            data: chartData
          }
        ]
      };

      option && myChart.setOption(option);

    }
  },
}
</script>

<style scoped lang="scss">
.CertificateChainContainer{
  margin-left: 20%;
  margin-right: 20%;
  width: 60%;
  height: 500px;
}
</style>