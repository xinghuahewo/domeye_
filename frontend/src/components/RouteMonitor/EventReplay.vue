<template>
  <div class="eventReplay">
    <template v-if="type === '路由泄漏' || type === '子前缀劫持'">
      <el-row justify="space-around" style="width: 100%;height: 100%">
        <el-col :span="16">
          <div class="before">
            <span><i class="el-icon-caret-right"></i>{{title1}}</span>
            <div style="width:100%;height:700px;margin-top: 10px" id="chart"></div>
          </div>
        </el-col>
        <el-col :span="7" style="height: 100%;overflow-y: scroll">
          <el-table :data="tableData">
            <el-table-column type="index" label="序号" :index="indexMethod" width="60" />
            <el-table-column prop="domain" label="网站域名" min-width="120"/>
            <el-table-column prop="domain_title" label="网站名称" min-width="130" />
          </el-table>
        </el-col>
      </el-row>
    </template>
    <template v-else-if="type === '国家中断' || type === '边界中断'">
      <div class="before" style="width:100%;height:100%;">
        <span><i class="el-icon-caret-right"></i>{{title1}}</span>
        <div style="width:100%;height:1000px;margin-top: 20px" id="chart"></div>
      </div>
    </template>
    <template v-else>
      <el-row justify="space-around" align="middle" style="width: 100%;height: 450px">
        <el-col :span="16">
          <div class="before">
            <span><i class="el-icon-caret-right"></i>{{title1}}</span>
            <svg id="before" width=100% height=450 font-size="9"></svg>
          </div>
        </el-col>
        <el-col :span="7" style="height: 450px;overflow-y: scroll">
          <el-table :data="tableData">
            <el-table-column type="index" label="序号" :index="indexMethod" width="60" />
            <template v-if="type === 'AS中断'">
              <el-table-column prop="outage_prefix" label="路由前缀" min-width="100"/>
              <!-- <el-table-column prop="domain_prefix" label="路由前缀" min-width="100"/> -->
            </template>
            <template v-else-if="type === '国家中断'">
              <el-table-column prop="domain" label="AS号" min-width="120"/>
              <el-table-column prop="domain_title" label="AS名" min-width="130" />
              <el-table-column prop="domain_title" label="归属机构" min-width="130" />
            </template>
            <template v-else>
              <el-table-column prop="domain" label="网站域名" min-width="120"/>
              <el-table-column prop="domain_title" label="网站名称" min-width="130" />
            </template>
          </el-table>
        </el-col>
      </el-row>
      <el-divider></el-divider>
      <div class="start">
        <!-- 选择事件发生时刻 -->
        <span><i class="el-icon-caret-right">{{title2}}</i></span>
        <svg id="start" width=100% height=450 font-size="9"></svg>
      </div>
    </template>
  </div>
</template>

<script>
import * as d3 from 'd3';
import dagreD3 from 'dagre-d3';
import * as echarts from "echarts"

export default {
  name: 'EventReplay',
  props: ['subdata', 'type'],	// subdata为整个事件

  data() {
    let title1 = '事件发生时：'
    let title2 = '事件发生前：'
    if(this.type === '路由泄漏'){
      title1 = '事件相关路径'
    }
    else if(this.type === '国家中断'){
      title1 = '国家中断拓扑图'
    }else if(this.type === '子前缀劫持'){
      title1 = '劫持拓扑图'
    }
    return {
      time: '',
      timeSelection: [],
      title1,
      title2,
      tableData: [],
    }
  },

  watch: {
    // subdata值变化时，watch监听到并且执行
    subdata(val) {
      // 通过判断是否有某字段来判断如何进行绘画
      if(this.type === '前缀劫持'){
        this.drawGraph1_hijack(val);
        this.drawGraph2_hijack(val);
      }
      else if(this.type === '前缀中断'){
        this.drawGraph1_pre_outage(val);
        this.drawGraph2_pre_outage(val);
      }
      else if (this.type === 'AS中断'){
        this.drawGraph1_as_outage(val);
        this.drawGraph2_as_outage(val);
      }
      else if(this.type === '路由泄漏'){
        // this.drawGraph_leak(val);
        this.drawLeakGraph(val);
      }
      else if(this.type === '边界中断'){
        this.drawBoundaryGraph(val)
      }
      else if(this.type === '子前缀劫持'){
        this.drawPreHijackGraph(val)
      }
      else if(this.type === '国家中断'){
        this.drawCountryOutageGraph(val)
      }

      if(this.type !== '边界中断' && this.type !== 'RPKI证书异常' && this.type !== '国家中断'){
        if(this.type === 'AS中断'){
          // console.log(this.type)
          // this.tableData = this.removeDuplicate(val.domain_list.map((item) => ({
          //   'domain_prefix': item.domain_prefix
          // })))
          if(val.outage_prefixes && val.outage_prefixes.length > 0) {
            this.tableData = val.outage_prefixes.map((prefix, index) => ({
              'outage_prefix': prefix,
              'id': index + 1
            }))
          }
        }
        else {
          if(val.domain_list.length > 10)
            this.tableData = val.domain_list.slice(0, 10)
          else
            this.tableData = val.domain_list
        }
      }
    },

    mounted(){

    },

    type(val){
      // console.log('type函数执行')
      if (val === '路由泄漏'){
          this.title1 = '事件相关路径'
          this.title2 = ''
      }
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
      const vp_paths = val.pre_vp_paths
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
          svg = d3.select("#start");
          svg.select('g').remove()  // remove previous nodes, clear the canvas
          inner = svg.append("g");
        } else {
          svg = d3.select("#start");
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
      const vp_paths = val.eve_vp_paths
      let nodes = [];
      const path_list = Object.values(vp_paths)[0]

      if(path_list.length !== 0) {
        // console.log(path_list.length)
        for (let i = 0; i < path_list.length; i++) {
          let nodes_ = path_list[i].split(" ")
          nodes = [...nodes, ...nodes_]
        }

        let moas_set = [val.hijacked_asn, val.hijacker_asn]

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
        g.node(moas_set[1]).style = "fill: rgb(212, 26, 26)";

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
          svg = d3.select("#before");
          svg.select('g').remove()  // remove previous nodes, clear the canvas
          inner = svg.append("g");
        } else {
          svg = d3.select("#before");
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
      const vp_paths = val.pre_vp_paths
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
          svg = d3.select("#start");
          svg.select('g').remove()  // remove previous nodes, clear the canvas
          inner = svg.append("g");
        } else {
          svg = d3.select("#start");
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
      const vp_paths = val.eve_vp_paths
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
          svg = d3.select("#before");
          svg.select('g').remove()  // remove previous nodes, clear the canvas
          inner = svg.append("g");
        } else {
          svg = d3.select("#before");
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

    drawGraph1_as_outage(val) {
      // 获取所有节点
      // console.log('开始获取所有节点...')
      // const vp_paths = eval("("+val.pre_vp_paths+")")
      const vp_paths = val.pre_vp_paths
      let nodes = [];
      const path_list = Object.values(vp_paths)[0]

      if(path_list.length !== 0) {
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
          svg = d3.select("#start");
          svg.select('g').remove()  // remove previous nodes, clear the canvas
          inner = svg.append("g");
        } else {
          svg = d3.select("#start");
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

    drawGraph2_as_outage(val) {
      // 获取所有节点
      // console.log('开始获取所有节点...')
      // const vp_paths = eval("("+val.pre_vp_paths+")")
      const vp_paths = val.eve_vp_paths
      let nodes = [];
      const path_list = Object.values(vp_paths)[0]

      if(path_list.length !== 0) {
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
          svg = d3.select("#before");
          svg.select('g').remove()  // remove previous nodes, clear the canvas
          inner = svg.append("g");
        } else {
          svg = d3.select("#before");
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

    drawGraph_leak(val) {
      const as_path = val.as_path
      let nodes = as_path.split(" ")

      let leak_by = val.leak_by
      let leak_to = val.leak_to

      if(nodes.length !== 0) {
        // 获取所有长度为1的节点间线段
        let linkArray = [];
        for (let j = 1; j < nodes.length; j++) {
          if (nodes[j - 1] !== nodes[j]) {
            linkArray.push({from: nodes[j - 1], to: nodes[j]});
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
            style: "stroke: darkgray; fill: Gainsboro",
            height: 25,
            width: 25,
          });
        }

        g.node(leak_to).style = "fill: LightBlue";
        g.node(leak_by).style = "fill: LightYellow";
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
          svg = d3.select("#before");
          svg.select('g').remove()  // remove previous nodes, clear the canvas
          inner = svg.append("g");
        } else {
          svg = d3.select("#before");
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

    // 数组去重
    removeDuplicate(arr) {
      const map = new Map()
      const newArr = []

      arr.forEach(item => {
        if (!map.has(item)) { // has()用于判断map是否包为item的属性值
          map.set(item, true) // 使用set()将item设置到map中，并设置其属性值为true
          newArr.push(item)
        }
      })

      return newArr
    },

    // 路由泄漏画图函数
    drawLeakGraph(val) {
      console.log("subdata的变化")
      console.log(val)
      let nodes = this.removeDuplicate(val.as_path.split(" "))

      let nodeDatas = []
      let lineDatas = []

      // 设置节点数据
      for (let i = 0; i < nodes.length; i++) {
        if(nodes[i] === val.leak_by){
          nodeDatas.push({
            name: 'AS' + nodes[i],
            x: (i+1)*15,
            y: 215,
            itemStyle: {
              color: '#d41a1a',
            },
            label: {
              position: 'bottom',
              formatter: [
                '{title|泄露方：{b}}{abg|}',
                '{value|' + val.leak_by_name + '}',
                '{value|' + val.leak_by_org + '}',
                '{value|' + val.leak_by_country + '}',
              ].join('\n'),
              backgroundColor: '#eee',
              borderColor: '#777',
              borderWidth: 1,
              borderRadius: 4,
              rich: {
                title: {
                  color: '#eee',
                  align: 'center'
                },
                abg: {
                  backgroundColor: '#1a5dd4',
                  width: '100%',
                  align: 'right',
                  height: 30,
                  borderRadius: [4, 4, 0, 0]
                },
                hr: {
                  borderColor: '#777',
                  width: '100%',
                  borderWidth: 0.5,
                  height: 0
                },
                value: {
                  padding: [5, 0, 5, 0],
                  align: 'center'
                },
                valueHead: {
                  color: '#333',
                  width: 20,
                  padding: [0, 20, 0, 30],
                  align: 'center'
                },
                rate: {
                  width: 40,
                  align: 'right',
                  padding: [0, 10, 0, 0]
                },
                rateHead: {
                  color: '#333',
                  width: 40,
                  align: 'center',
                  padding: [0, 10, 0, 0]
                }
              }
            }
          })
        }
        else if(nodes[i] === val.leak_to){
          nodeDatas.push({
            name: 'AS' + nodes[i],
            x: (i+1)*15,
            y: 200,
            itemStyle: {
              color: '#ff7d37',
            },
            label: {
              position: 'top',
              formatter: [
                '{title|传播方：{b}}{abg|}',
                '{value|' + val.leak_to_name + '}',
                '{value|' + val.leak_to_org + '}',
                '{value|' + val.leak_to_country + '}',
              ].join('\n'),
              backgroundColor: '#eee',
              borderColor: '#777',
              borderWidth: 1,
              borderRadius: 4,
              rich: {
                title: {
                  color: '#eee',
                  align: 'center'
                },
                abg: {
                  backgroundColor: '#1a5dd4',
                  width: '100%',
                  align: 'right',
                  height: 30,
                  borderRadius: [4, 4, 0, 0]
                },
                hr: {
                  borderColor: '#777',
                  width: '100%',
                  borderWidth: 0.5,
                  height: 0
                },
                value: {
                  padding: [5, 0, 5, 0],
                  align: 'center'
                },
                valueHead: {
                  color: '#333',
                  width: 20,
                  padding: [0, 20, 0, 30],
                  align: 'center'
                },
                rate: {
                  width: 40,
                  align: 'right',
                  padding: [0, 10, 0, 0]
                },
                rateHead: {
                  color: '#333',
                  width: 40,
                  align: 'center',
                  padding: [0, 10, 0, 0]
                }
              }
            }
          })
        }
        else if(nodes[i] === val.attacked_as){
          nodeDatas.push({
            name: 'AS' + nodes[i],
            x: (i+1)*15,
            y: 200,
            itemStyle: {
              color: '#1ad45b',
            },
            label: {
              position: 'top',
              formatter: [
                '{title|受害方：{b}}{abg|}',
                '{value|' + val.ori_as_name + '}',
                '{value|' + val.ori_as_org + '}',
                '{value|' + val.ori_as_country + '}',
              ].join('\n'),
              backgroundColor: '#eee',
              borderColor: '#777',
              borderWidth: 1,
              borderRadius: 4,
              rich: {
                title: {
                  color: '#eee',
                  align: 'center'
                },
                abg: {
                  backgroundColor: '#1a5dd4',
                  width: '100%',
                  align: 'right',
                  height: 30,
                  borderRadius: [4, 4, 0, 0]
                },
                hr: {
                  borderColor: '#777',
                  width: '100%',
                  borderWidth: 0.5,
                  height: 0
                },
                value: {
                  padding: [5, 10, 5, 10],
                  align: 'center'
                },
              }
            }
          })
        }
        else{
          nodeDatas.push({
            name: 'AS' + nodes[i],
            x: (i+1)*15,
            y: 200,
            itemStyle: {
              color: '#99a9bf',
            },
          })
        }


      }

      // 设置连线数据
      for (let i = 0; i < nodes.length - 1; i++) {
        lineDatas.push({
          source: 'AS' + nodes[i+1],
          target: 'AS' + nodes[i]
        },)
      }

      let chartDom = document.getElementById('chart')
      let myChart = echarts.init(chartDom)

      let option = {
        animationDurationUpdate: 1500,
        animationEasingUpdate: 'quinticInOut',
        series: [
          {
            type: 'graph',
            layout: 'none',
            symbolSize: 50,
            roam: true,
            label: {
              show: true
            },
            edgeSymbol: ['circle', 'arrow'],
            edgeSymbolSize: [4, 10],
            edgeLabel: {
              fontSize: 20
            },
            data: nodeDatas,
            links: lineDatas,
            lineStyle: {
              opacity: 0.9,
              width: 2,
              curveness: 0
            }
          }
        ]
      };

      option && myChart.setOption(option);
    },

    // 边界中断
    drawBoundaryGraph(val) {
      let chartDom = document.getElementById('chart')
      const existed = echarts.getInstanceByDom(chartDom)
      if (existed) {
        existed.dispose()
      }
      let myChart = echarts.init(chartDom)

      let data = val.graph

      const nodes = (data.nodes || []).map((node) => ({
        ...node,
        symbolSize: 35,
      }))

      let option = {
        series: [
          {
            //name: 'AS_outage',
            type: 'graph',
            layout: 'force',
            data: nodes,
            links: data.links || [],
            roam: true,
            edgeSymbol: ['arrow'],
            label: {
              show: true,
              position:'' ,
            },
            force: {
              repulsion: 1000
            },
            lineStyle: {
              color: 'target',
              curveness: 0
            }
          }
        ]
      };

      option && myChart.setOption(option);
    },

    // 国家中断（美化版）
    drawCountryOutageGraph(val) {
      const chartDom = document.getElementById('chart')
      const existed = echarts.getInstanceByDom(chartDom)
      if (existed) {
        existed.dispose()
      }
      const myChart = echarts.init(chartDom)

      const data = val.graph || { nodes: [], links: [] }
      const outageSet = new Set((val.outage_ases || []).map((asn) => String(asn)))

      const degreeMap = new Map()
      data.nodes.forEach((node) => {
        degreeMap.set(String(node.name), 0)
      })
      data.links.forEach((link) => {
        const source = String(link.source)
        const target = String(link.target)
        degreeMap.set(source, (degreeMap.get(source) || 0) + 1)
        degreeMap.set(target, (degreeMap.get(target) || 0) + 1)
      })

      const degrees = Array.from(degreeMap.values())
      const maxDegree = degrees.length ? Math.max(...degrees) : 0
      const labelThreshold = Math.max(3, Math.floor(maxDegree * 0.6))

      const getNodeSize = (degree) => {
        if (!maxDegree) return 10
        const scaled = Math.sqrt(degree / maxDegree)
        return 8 + scaled * 20
      }

      const nodes = (data.nodes || []).map((node) => {
        const id = String(node.name)
        const degree = degreeMap.get(id) || 0
        const isOutage = outageSet.has(id)
        return {
          name: id,
          value: degree,
          symbolSize: getNodeSize(degree),
          draggable: true,
          itemStyle: {
            color: isOutage ? '#ff4d4f' : '#3b82f6',
            borderColor: isOutage ? '#ffd1d1' : '#93c5fd',
            borderWidth: isOutage ? 2 : 1,
            shadowBlur: isOutage ? 12 : 6,
            shadowColor: isOutage ? 'rgba(255, 77, 79, 0.45)' : 'rgba(59, 130, 246, 0.35)',
          },
          label: {
            show: isOutage || degree >= labelThreshold,
            formatter: (p) => `AS${p.name}`,
            color: isOutage ? '#ffe4e6' : '#dbeafe',
            fontSize: isOutage ? 12 : 10,
          },
        }
      })

      const links = (data.links || []).map((link) => ({
        source: String(link.source),
        target: String(link.target),
      }))

      const option = {
        backgroundColor: '#0b1221',
        title: {
          text: `国家拓扑  节点:${nodes.length}  边:${links.length}  中断:${outageSet.size}`,
          left: 18,
          top: 12,
          textStyle: {
            color: '#e2e8f0',
            fontSize: 14,
            fontWeight: 'normal',
          },
        },
        tooltip: {
          trigger: 'item',
          backgroundColor: 'rgba(15, 23, 42, 0.9)',
          borderColor: '#1f2937',
          textStyle: {
            color: '#e2e8f0',
          },
          formatter: (params) => {
            if (params.dataType === 'edge') {
              return `连线: AS${params.data.source} → AS${params.data.target}`
            }
            const degree = params.data.value || 0
            const status = outageSet.has(params.data.name) ? '中断' : '正常'
            return [
              `AS${params.data.name}`,
              `连接度: ${degree}`,
              `状态: ${status}`,
            ].join('<br/>')
          },
        },
        series: [
          {
            type: 'graph',
            layout: 'force',
            data: nodes,
            links: links,
            roam: true,
            emphasis: {
              focus: 'adjacency',
              label: {
                show: true,
              },
            },
            force: {
              repulsion: 180,
              edgeLength: [40, 120],
              gravity: 0.12,
            },
            lineStyle: {
              color: 'rgba(148, 163, 184, 0.45)',
              width: 1,
              curveness: 0.15,
            },
          },
        ],
      }

      myChart.setOption(option)
    },

    // 子前缀劫持画图函数
    drawPreHijackGraph(val) {
      let nodeDatas = [
        {
          name: 'AS' + val.hijacked_as_info[0].as,
          x: 15,
          y: 200,
          itemStyle: {
            color: '#d41a1a',
          },
          label: {
            position: 'top',
            formatter: [
              '{title|父前缀：{b}}{abg|}',
              '{value|' + val.hijacked_as_info[0].as_country + '}',
              '{value|' + val.hijacked_as_info[0].as_name + '}',
              '{value|' + val.hijacked_as_info[0].as_org + '}',
              '{value|' + val.hijacked_prefix + '}',
            ].join('\n'),
            backgroundColor: '#eee',
            borderColor: '#777',
            borderWidth: 1,
            borderRadius: 4,
            rich: {
              title: {
                color: '#eee',
                align: 'center'
              },
              abg: {
                backgroundColor: '#1a5dd4',
                width: '100%',
                align: 'right',
                height: 30,
                borderRadius: [4, 4, 0, 0]
              },
              value: {
                padding: [5, 10, 5, 10],
                align: 'center'
              },
            }
          }
        },
        {
          name: 'AS' + val.hijacker_as_info[0].as,
          x: 30,
          y: 200,
          itemStyle: {
            color: '#1ad45b',
          },
          label: {
            position: 'top',
            formatter: [
              '{title|子前缀：{b}}{abg|}',
              '{value|' + val.hijacker_as_info[0].as_country + '}',
              '{value|' + val.hijacker_as_info[0].as_name + '}',
              '{value|' + val.hijacker_as_info[0].as_org + '}',
              '{value|' + val.hijacker_prefix + '}',
            ].join('\n'),
            backgroundColor: '#eee',
            borderColor: '#777',
            borderWidth: 1,
            borderRadius: 4,
            rich: {
              title: {
                color: '#eee',
                align: 'center'
              },
              abg: {
                backgroundColor: '#1a5dd4',
                width: '100%',
                align: 'right',
                height: 30,
                borderRadius: [4, 4, 0, 0]
              },
              value: {
                padding: [5, 10, 5, 10],
                align: 'center'
              },
            }
          }
        },
      ]
      let lineDatas = [
        {
          target: 'AS' + val.hijacked_as_info[0].as,
          source: 'AS' + val.hijacker_as_info[0].as,
        }
      ]

      let chartDom = document.getElementById('chart')
      let myChart = echarts.init(chartDom)

      let option = {
        animationDurationUpdate: 1500,
        animationEasingUpdate: 'quinticInOut',
        series: [
          {
            type: 'graph',
            layout: 'none',
            symbolSize: 50,
            roam: true,
            label: {
              show: true
            },
            edgeSymbol: ['circle', 'arrow'],
            edgeSymbolSize: [4, 10],
            edgeLabel: {
              fontSize: 20
            },
            data: nodeDatas,
            links: lineDatas,
            lineStyle: {
              opacity: 0.9,
              width: 2,
              curveness: 0
            }
          }
        ]
      };

      option && myChart.setOption(option);
    },

    indexMethod(index) {
      return index + 1
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
  .before {
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
  .start {
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
