<template>
  <div class="eventReplay">
    <div class="before1">
      <span><i class="el-icon-caret-right"></i>{{title1}}</span>
      <svg id="before1" width=100% height=450 font-size="9"></svg>
    </div>
    <el-divider></el-divider>
    <div class="start1">
      <!-- 选择事件发生时刻 -->
      <span><i class="el-icon-caret-right">{{title2}}</i></span>
      <svg id="start1" width=100% height=450 font-size="9"></svg>
    </div>
  </div>
</template>

<script>
import * as d3 from 'd3';
import dagreD3 from 'dagre-d3';

export default {
  name: 'EventRecover',
  props: ['subdata', 'type'],	// subdata为整个事件

  created() {
    console.log("this.subdata")
    console.log(this.subdata)
  },
  data() {
    return {
      time: '',
      timeSelection: [],
      title1: '事件发生时',
      title2: '事件发生后',
    }
  },

  methods: {
    RandomColor() {
      let r, g, b;
      r = Math.floor(Math.random() * 254)
      g = Math.floor(Math.random() * 254)
      b = Math.floor(Math.random() * 254)
      return "rgb("+ r + "," + g + "," + b +")"
    },

    drawGraph1_hijack(val) {
      // 获取所有节点
      const vp_paths = val.eve_vp_paths
      let nodes = [];
      const path_list = Object.values(vp_paths)[0]

      // console.log(path_list.length)
      if(path_list.length !== 0) {
        for (let i = 0; i < path_list.length; i++) {
          let nodes_ = path_list[i].split(" ")
          nodes = [...nodes, ...nodes_]
        }

        let moas_set = [val.hijacked_asn]

        // 获取所有长度为1的节点间线段
        let linkArray = [];
        for (let i = 0; i < path_list.length; i++) {
          let path = path_list[i].split(" ")
          for (let j = 1; j < path.length; j++) {
            if (path[j - 1] !== path[j]) {
              linkArray.push({from: path[j - 1], to: path[j]});
            }
          }
        }

        const options = {
          rankdir: nodes.length > 10 ? "BT" : "LR",
          ranksep: 25,  // 节点之间线距离
          nodesep: 30,  // 节点之间距离
          marginx: 8,
        };

        // Create the input graph Object
        const g = new dagreD3.graphlib.Graph()
            .setGraph(options)
            .setDefaultEdgeLabel(function () {
              return {};
            });

        // Set nodes
        let n_nodes = nodes.length
        for (let i = 0; i < n_nodes; i++) {
          g.setNode(nodes[i], {
            label: 'AS' + nodes[i],
            shape: "circle",
            style: "stroke: darkgray; fill: Gainsboro",
            height: 25,
            width: 25,
          });
        }

        g.node(moas_set[0]).style = "fill: rgb(212, 26, 26)";

        // Set edges
        let n_edges = linkArray.length;
        for (let i = 0; i < n_edges; i++) {
          let edge = linkArray[i];
          g.setEdge(edge['from'], edge['to'], {
            style: "stroke: rgb(20, 20, 20); fill: none;",
            arrowheadStyle: "fill: rgb(20, 20, 20); stroke: rgb(20, 20, 20);",
            arrowhead: 'vee'
          });
        }

        // // 创建渲染器
        const render = new dagreD3.render();

        let svg = null
        let inner = null
        // Set up an SVG group so that we can translate the final graph.
        if (document.getElementsByTagName('g')) {
          svg = d3.select("#before1");
          svg.select('g').remove()  // remove previous nodes, clear the canvas
          inner = svg.append("g");
        } else {
          svg = d3.select("#before1");
          inner = svg.append("g");
        }

        // 设置图像大小可拖动、缩放
        const handleZoom = e => inner.attr('transform', e.transform)
        const zoom = d3.zoom().on('zoom', handleZoom)
        svg.call(zoom)

        // Run the renderer. This is what draws the final graph.
        render(inner, g);
        // console.log('图片绘制成功')
      }
    },

    drawGraph2_hijack(val) {
      // 获取所有节点

      // const vp_paths = eval("("+val.pre_vp_paths+")")
      const vp_paths = val.next_vp_paths
      let nodes = [];
      const path_list = Object.values(vp_paths)[0]

      if(path_list.length !== 0) {
        // console.log(path_list.length)
        for (let i = 0; i < path_list.length; i++) {
          let nodes_ = path_list[i].split(" ")
          nodes = [...nodes, ...nodes_]
        }

        let moas_set = [val.hijacked_asn]

        // 获取所有长度为1的节点间线段
        let linkArray = [];
        for (let i = 0; i < path_list.length; i++) {
          let path = path_list[i].split(" ")
          for (let j = 1; j < path.length; j++) {
            if (path[j - 1] !== path[j]) {
              linkArray.push({from: path[j - 1], to: path[j]});
            }
          }
        }

        const options = {
          rankdir: nodes.length > 10 ? "BT" : "LR",
          ranksep: 25,  // 节点之间线距离
          nodesep: 30,  // 节点之间距离
          marginx: 8,
        };

        // Create the input graph Object
        const g = new dagreD3.graphlib.Graph()
            .setGraph(options)
            .setDefaultEdgeLabel(function () {
              return {};
            });

        // Set nodes
        let n_nodes = nodes.length
        for (let i = 0; i < n_nodes; i++) {
          g.setNode(nodes[i], {
            label: 'AS' + nodes[i],
            shape: "circle",
            style: "stroke: darkgray; fill: Gainsboro",
            height: 25,
            width: 25,
          });
        }

        g.node(moas_set[0]).style = "fill: rgb(26, 212, 91)";

        // Set edges
        let n_edges = linkArray.length;
        for (let i = 0; i < n_edges; i++) {
          let edge = linkArray[i];
          g.setEdge(edge['from'], edge['to'], {
            style: "stroke: rgb(20, 20, 20); fill: none;",
            arrowheadStyle: "fill: rgb(20, 20, 20); stroke: rgb(20, 20, 20);",
            arrowhead: 'vee'
          });
        }

        // // 创建渲染器
        const render = new dagreD3.render();


        let svg = null
        let inner = null
        // Set up an SVG group so that we can translate the final graph.
        if (document.getElementsByTagName('g')) {
          svg = d3.select("#start1");
          svg.select('g').remove()  // remove previous nodes, clear the canvas
          inner = svg.append("g");
        } else {
          svg = d3.select("#start1");
          inner = svg.append("g");
        }

        // 设置图像大小可拖动、缩放
        const handleZoom = e => inner.attr('transform', e.transform)
        const zoom = d3.zoom().on('zoom', handleZoom)
        svg.call(zoom)

        // Run the renderer. This is what draws the final graph.
        render(inner, g);
        // console.log('图片绘制成功')
      }
    },

    drawGraph1_pre_outage(val) {
      // 获取所有节点
      // console.log('开始获取所有节点...')
      // const vp_paths = eval("("+val.pre_vp_paths+")")
      const vp_paths = val.eve_vp_paths
      let nodes = [];
      const path_list = Object.values(vp_paths)[0]

      if(path_list.length !== 0) {
        // console.log(path_list.length)
        for (let i = 0; i < path_list.length; i++) {
          let nodes_ = path_list[i].split(" ")
          nodes = [...nodes, ...nodes_]
        }
        // console.log(nodes)

        // let moas_set = [val.hijacked_asn]
        // console.log('moas_set: ' + val.hijacked_asn);
        let outage_as = val.asn

        // 获取所有长度为1的节点间线段
        let linkArray = [];
        for (let i = 0; i < path_list.length; i++) {
          let path = path_list[i].split(" ")
          for (let j = 1; j < path.length; j++) {
            if (path[j - 1] !== path[j]) {
              linkArray.push({from: path[j - 1], to: path[j]});
            }
          }
        }
        // console.log(linkArray)

        const options = {
          rankdir: nodes.length > 10 ? "BT" : "LR",
          ranksep: 25,  // 节点之间线距离
          nodesep: 30,  // 节点之间距离
          marginx: 8,
        };

        // Create the input graph Object
        const g = new dagreD3.graphlib.Graph()
            .setGraph(options)
            .setDefaultEdgeLabel(function () {
              return {};
            });
        // console.log('对象g创建成功');

        // Set nodes
        let n_nodes = nodes.length
        for (let i = 0; i < n_nodes; i++) {
          g.setNode(nodes[i], {
            label: 'AS' + nodes[i],
            shape: "circle",
            style: nodes[i] === outage_as ? "stroke: darkgray; fill: LightBlue" : "stroke: darkgray; fill: Gainsboro",
            height: 25,
            width: 25,
          });
        }
        // console.log('节点设置成功');

        // console.log(nodes)
        // console.log(outage_as)
        g.node(outage_as).style = "fill: LightBlue";
        // console.log('hijacked_as设置成功');

        // Set edges
        let n_edges = linkArray.length;
        for (let i = 0; i < n_edges; i++) {
          let edge = linkArray[i];
          g.setEdge(edge['from'], edge['to'], {
            style: "stroke: rgb(20, 20, 20); fill: none;",
            arrowheadStyle: "fill: rgb(20, 20, 20); stroke: rgb(20, 20, 20);",
            arrowhead: 'vee'
          });
        }
        // console.log('边设置成功')

        // // 创建渲染器
        const render = new dagreD3.render();


        let svg = null
        let inner = null
        // Set up an SVG group so that we can translate the final graph.
        if (document.getElementsByTagName('g')) {
          svg = d3.select("#before1");
          svg.select('g').remove()  // remove previous nodes, clear the canvas
          inner = svg.append("g");
        } else {
          svg = d3.select("#before1");
          inner = svg.append("g");
        }

        // 设置图像大小可拖动、缩放
        const handleZoom = e => inner.attr('transform', e.transform)
        const zoom = d3.zoom().on('zoom', handleZoom)
        svg.call(zoom)

        // Run the renderer. This is what draws the final graph.
        render(inner, g);
        // console.log('图片绘制成功')
      }
    },

    drawGraph2_pre_outage(val) {
      // 获取所有节点
      // console.log('开始获取所有节点...')
      // const vp_paths = eval("("+val.pre_vp_paths+")")
      const vp_paths = val.next_vp_paths
      let nodes = [];
      const path_list = Object.values(vp_paths)[0]

      if(path_list.length !== 0){
        // console.log(path_list.length)
        for(let i=0; i<path_list.length; i++){
          let nodes_ = path_list[i].split(" ")
          nodes = [...nodes, ...nodes_]
        }
        // console.log(nodes)

        // let moas_set = [val.hijacked_asn]
        // console.log('moas_set: ' + val.hijacked_asn);
        let outage_as = val.asn

        // 获取所有长度为1的节点间线段
        let linkArray = [];
        for (let i=0; i<path_list.length; i++){
          let path = path_list[i].split(" ")
          for(let j=1; j<path.length; j++){
            if (path[j-1] !== path[j]){
              linkArray.push({ from: path[j-1], to: path[j] });
            }
          }
        }
        // console.log(linkArray)

        const options = {
          rankdir: nodes.length > 10 ? "BT" : "LR",
          ranksep: 25,  // 节点之间线距离
          nodesep: 30,  // 节点之间距离
          marginx: 8,
        };

        // Create the input graph Object
        const g = new dagreD3.graphlib.Graph()
            .setGraph(options)
            .setDefaultEdgeLabel(function () {
              return {};
            });
        // console.log('对象g创建成功');

        // Set nodes
        let n_nodes = nodes.length
        for (let i = 0; i < n_nodes; i++) {
          g.setNode(nodes[i], {
            label: 'AS'+ nodes[i],
            shape: "circle",
            style: "stroke: darkgray; fill: Gainsboro",
            height: 25,
            width: 25,
          });
        }
        // console.log('节点设置成功');

        g.node(outage_as).style = "fill: LightBlue";
        // console.log('hijacked_as设置成功');

        // Set edges
        let n_edges = linkArray.length;
        for (let i = 0; i < n_edges; i++) {
          let edge = linkArray[i];
          g.setEdge(edge['from'], edge['to'], {
            style: "stroke: rgb(20, 20, 20); fill: none;",
            arrowheadStyle: "fill: rgb(20, 20, 20); stroke: rgb(20, 20, 20);",
            arrowhead: 'vee'
          });
        }
        // console.log('边设置成功')

        // // 创建渲染器
        const render = new dagreD3.render();


        let svg = null
        let inner = null
        // Set up an SVG group so that we can translate the final graph.
        if (document.getElementsByTagName('g')) {
          svg = d3.select("#start1");
          svg.select('g').remove()  // remove previous nodes, clear the canvas
          inner = svg.append("g");
        } else {
          svg = d3.select("#start1");
          inner = svg.append("g");
        }

        // 设置图像大小可拖动、缩放
        const handleZoom = e => inner.attr('transform', e.transform)
        const zoom = d3.zoom().on('zoom', handleZoom)
        svg.call(zoom)

        // Run the renderer. This is what draws the final graph.
        render(inner, g);
      }
    },
  },

  watch: {
    // subdata值变化时，watch监听到并且执行
    subdata(val) {
      // 通过判断是否有某字段来判断如何进行绘画
      if (this.type === '前缀劫持') {
        this.drawGraph1_hijack(val);
        if(Object.values(val.next_vp_paths).length > 0)
          this.drawGraph2_hijack(val);
      } else if (this.type === '前缀中断') {
        this.drawGraph1_pre_outage(val);
        if(Object.values(val.next_vp_paths).length > 0)
          this.drawGraph2_pre_outage(val);
      }
    }
  },

  mounted() {
    if(this.type === '前缀劫持'){
      this.drawGraph1_hijack(this.subdata);
      console.log(this.subdata.next_vp_paths)
      if(Object.values(this.subdata.next_vp_paths).length > 0)
        this.drawGraph2_hijack(this.subdata);
    }
    else if(this.type === '前缀中断'){
      this.drawGraph1_pre_outage(this.subdata);
      if(Object.values(this.subdata.next_vp_paths).length > 0)
        this.drawGraph2_pre_outage(this.subdata);
    }
  },
}
</script>

<style lang="scss" scoped>
.eventReplay {
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: space-around;
  align-items: center;
  .before1 {
    //height: 500px;
    width: 95%;
    span {
      padding-top: 35px;
      display: float;
      float: left;
      padding-left: 6px;
      font-size: 16px;
      margin-bottom: 30px;
    }
  }
  .start1 {
    margin-top: 0px;
    //height: 500px;
    width: 95%;
    span {
      display: float;
      float: left;
      padding-left: 6px;
      font-size: 16px;
      margin-bottom: 30px;
    }
    .el-select {
      margin-left: 10px;
      & :deep .is-focus .el-input__inner  {
        border: 1px solid rgb(167, 167, 167);
      }
      & :deep .el-input__inner:focus {
        border: 1px solid rgb(167, 167, 167);
      }
    }
  }
  .el-divider {
    width: 95%;
  }
}
</style>