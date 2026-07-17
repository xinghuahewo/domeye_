import { RouteRecordRaw } from 'vue-router';

/**
 * 建议：路由 path 路径与文件夹名称相同，找文件可浏览器地址找，方便定位文件位置
 *
 * 路由meta对象参数说明
 * meta: {
 *      title:          菜单栏及 tagsView 栏、菜单搜索名称（国际化）
 *      isLink：        是否超链接菜单，开启外链条件，`1、isLink: 链接地址不为空 2、isIframe:false`
 *      isHide：        是否隐藏此路由
 *      isKeepAlive：   是否缓存组件状态
 *      isAffix：       是否固定在 tagsView 栏上
 *      isIframe：      是否内嵌窗口，开启条件，`1、isIframe:true 2、isLink：链接地址不为空`
 *      roles：         当前路由权限标识，取角色管理。控制路由显示、隐藏。超级管理员：admin 普通角色：common
 *      icon：          菜单、tagsView 图标，阿里：加 `iconfont xxx`，fontawesome：加 `fa xxx`
 * }
 */

// 扩展 RouteMeta 接口
declare module 'vue-router' {
  interface RouteMeta {
    title?: string;
    isLink?: string;
    isHide?: boolean;
    isKeepAlive?: boolean;
    isAffix?: boolean;
    isIframe?: boolean;
    roles?: string[];
    icon?: string;
  }
}

/**
 * 定义动态路由
 * 前端添加路由，请在顶级节点的 `children 数组` 里添加
 * @description 未开启 isRequestRoutes 为 true 时使用（前端控制路由），开启时第一个顶级 children 的路由将被替换成接口请求回来的路由数据
 * @description 各字段请查看 `/@/views/system/menu/component/addMenu.vue 下的 ruleForm`
 * @returns 返回路由菜单数据
 */
export const dynamicRoutes: Array<RouteRecordRaw> = [
  {
    path: '/',
    name: '/',
    component: () => import('/@/layout/index.vue'),
    redirect: '/home',
    meta: {
      isKeepAlive: false,
    },
    children: [
      {
        path: '/home',
        name: 'home',
        component: () => import('/@/views/home/index.vue'),
        meta: {
          title: '路由异常应急处置系统',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: true,
          // isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          // icon: 'iconfont icon-shouye',
          icon: 'ele-HomeFilled',
        },
      },
      {
        path: '/anomaly',
        name: 'anomaly',
        component: () => import('/@/layout/routerView/parent.vue'),
        redirect: '/anomaly/judge',
        meta: {
          title: '国内路由异常',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          // icon: 'iconfont icon-xitongshezhi',
          icon: 'ele-Setting',
        },
        children: [
          {
            path: '/anomaly/judge',
            name: 'anomaly_judge',
            component: () => import('/@/newviews/anomaly/judge.vue'),
            meta: {
              title: '待研判事件',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-Menu',
            },
          },
          {
            path: '/anomaly/notify',
            name: 'anomaly_notify',
            component: () => import('/@/newviews/anomaly/notify.vue'),
            meta: {
              title: '待通报事件',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-ColdDrink',
            },
          },
          {
            path: '/anomaly/notified',
            name: 'anomaly_notified',
            component: () => import('/@/newviews/anomaly/notified.vue'),
            meta: {
              title: '已通报事件',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-ColdDrink',
            },
          },
          {
            path: '/anomaly/suspected',
            name: 'anomaly_suspected',
            component: () => import('/@/newviews/anomaly/suspected.vue'),
            meta: {
              title: '疑似事件',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-Menu',
            },
          },
          {
            path: '/anomaly/misreport',
            name: 'anomaly_misreport',
            component: () => import('/@/newviews/anomaly/misreport.vue'),
            meta: {
              title: '误报事件',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-icon-',
              icon: 'ele-User',
            },
          },
          {
            path: '/anomaly/detail',
            name: 'anomaly_detail',
            component: () => import('/@/newviews/anomaly/detail.vue'),
            meta: {
              title: '事件详情',
              isLink: '',
              isHide: true,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-ColdDrink',
            },
          },
        ],
      },
      {
        path: '/abroad',
        name: 'abroad',
        component: () => import('/@/layout/routerView/parent.vue'),
        redirect: '/abroad/prefix_hijack',
        meta: {
          title: '国外路由异常',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          // icon: 'iconfont icon-neiqianshujuchucun',
          icon: 'ele-DataLine',
        },
        children: [
          {
            path: '/abroad/prefix_hijack',
            name: 'abroad_prefix_hijack',
            component: () => import('/@/newviews/abroad/index.vue'),
            meta: {
              title: '前缀劫持',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-Menu',
            },
          },
          {
            path: '/abroad/subprefix_hijack',
            name: 'abroad_subprefix_hijack',
            component: () => import('/@/newviews/abroad/index.vue'),
            meta: {
              title: '子前缀劫持',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-Menu',
            },
          },
          {
            path: '/abroad/prefix_outage',
            name: 'abroad_prefix_outage',
            component: () => import('/@/newviews/abroad/index.vue'),
            meta: {
              title: '前缀中断',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-Menu',
            },
          },
          {
            path: '/abroad/as_outage',
            name: 'abroad_as_outage',
            component: () => import('/@/newviews/abroad/index.vue'),
            meta: {
              title: 'AS中断',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-Menu',
            },
          },
          {
            path: '/abroad/country_outage',
            name: 'abroad_country_outage',
            component: () => import('/@/newviews/abroad/index.vue'),
            meta: {
              title: '国家中断',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-Menu',
            },
          },
          {
            path: '/abroad/route_leak',
            name: 'abroad_route_leak',
            component: () => import('/@/newviews/abroad/index.vue'),
            meta: {
              title: '路由泄漏',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-Menu',
            },
          },
        ],
      },
      {
        path: '/country',
        name: 'country',
        component: () => import('/@/layout/routerView/parent.vue'),
        redirect: '/country/border',
        meta: {
          title: '国家连通',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          // icon: 'iconfont icon-caidan',
          icon: 'ele-Menu',
        },
        children: [
          {
            path: '/country/border',
            name: 'country_border',
            component: () => import('/@/newviews/country/border.vue'),
            meta: {
              title: '国家边界',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-Menu',
            },
          },
          {
            path: '/country/connect',
            name: 'country_connect',
            component: () => import('/@/newviews/country/connect.vue'),
            meta: {
              title: '国与国连通',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-ColdDrink',
            },
          },
          {
            path: '/country/interruptevents',
            name: 'country_interruptevents',
            component: () => import('/@/newviews/country/InterruptEvents.vue'),
            meta: {
              title: '边界中断事件',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-Menu',
            },
          },
        ],
      },
      {
        path: '/feature',
        name: 'feature',
        component: () => import('/@/layout/routerView/parent.vue'),
        redirect: '/feature/countryFeature',
        meta: {
          title: '时序特征',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          icon: 'ele-TrendCharts',
        },
        children: [
          {
            path: '/feature/countryFeature',
            name: 'countryFeature',
            component: () => import('/@/newviews/feature/countryFeature.vue'),
            meta: {
              title: '国家时序特征',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-Basketball',
            },
          },
          {
            path: '/feature/ASFeature',
            name: 'ASFeature',
            component: () => import('/@/newviews/feature/ASFeature.vue'),
            meta: {
              title: 'AS时序特征',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-DataBoard',
            },
          },
          {
            path: '/feature/countryDetail',
            name: 'countryFeatureDetail',
            component: () => import('/@/newviews/feature/FeatureDetail.vue'),
            meta: {
              title: '国家时序特征详情',
              isLink: '',
              isHide: true,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-TrendCharts',
            },
          },
          {
            path: '/feature/asDetail',
            name: 'asFeatureDetail',
            component: () => import('/@/newviews/feature/FeatureDetail.vue'),
            meta: {
              title: 'AS时序特征详情',
              isLink: '',
              isHide: true,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              icon: 'ele-TrendCharts',
            },
          }
        ]
      },
      {
        path: '/node-status',
        name: 'nodeStatus',
        component: () => import('/@/newviews/nodeStatus/index.vue'),
        meta: {
          title: '观测节点状态',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          icon: 'ele-Monitor',
        },
      },
      {
        path: '/data-query',
        name: 'dataQuery',
        component: () => import('/@/newviews/dataQuery/index.vue'),
        meta: {
          title: '数据查询',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          icon: 'ele-Files',
        },
      },
      {
        path: '/RPKI',
        name: 'RPKI',
        component: () => import('/@/layout/routerView/parent.vue'),
        redirect: '/RPKI/page1',
        meta: {
          title: 'RPKI监测',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          // icon: 'iconfont icon-neiqianshujuchucun',
          icon: 'ele-DataAnalysis',
        },
        children: [
          {
            path: '/RPKI/page1',
            name: 'rpki_page1',
            component: () => import('/@/newviews/RPKI/page1.vue'),
            meta: {
              title: 'ROA数据统计',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-DataAnalysis',
            },
          },
          {
            path: '/RPKI/page2',
            name: 'rpki_page2',
            component: () => import('/@/newviews/RPKI/page2.vue'),
            meta: {
              title: 'CER证书',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-DataAnalysis',
            },
          },
        ]
      },
      {
        path: '/credit',
        name: 'credit',
        component: () => import('/@/layout/routerView/parent.vue'),
        redirect: '/credit/rank',
        meta: {
          title: '自治域信誉',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          // icon: 'iconfont icon-neiqianshujuchucun',
          icon: 'ele-ScaleToOriginal',
        },
        children: [
          {
            path: '/credit/rank',
            name: 'credit_rank',
            component: () => import('/@/newviews/credit/index.vue'),
            meta: {
              title: '自治域信誉排行',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-DataBoard',
            },
          },
          {
            path: '/credit/detail',
            name: 'credit_detail',
            component: () => import('/@/newviews/credit/detail.vue'),
            meta: {
              title: '自治域信誉详情',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: [],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-DataBoard',
            },
          },
        ]
      },
      {
        path: '/malicious',
        name: 'malicious',
        component: () => import('/@/layout/routerView/parent.vue'),
        redirect: '/malicious/longtime',
        meta: {
          title: '恶意AS',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          // icon: 'iconfont icon-neiqianshujuchucun',
          icon: 'ele-Filter',
        },
        children: [
          {
            path: '/malicious/longtime',
            name: 'malicious_longtime',
            component: () => import('/@/newviews/malicious/longtime.vue'),
            meta: {
              title: '长时恶意AS',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-DataAnalysis',
            },
          },
          {
            path: '/malicious/long_detail',
            name: 'malicious_long_detail',
            component: () => import('/@/newviews/malicious/long_detail.vue'),
            meta: {
              title: '长时恶意AS详情',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: [],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-DataBoard',
            },
          },
          {
            path: '/malicious/shorttime',
            name: 'malicious_shorttime',
            component: () => import('/@/newviews/malicious/shorttime.vue'),
            meta: {
              title: '短时恶意AS',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-DataBoard',
            },
          },
          {
            path: '/malicious/short_detail',
            name: 'malicious_short_detail',
            component: () => import('/@/newviews/malicious/short_detail.vue'),
            meta: {
              title: '短时恶意AS详情',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: [],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-DataBoard',
            },
          },
        ]
      },
      {
        path: '/importserver',
        name: 'importserver',
        component: () => import('/@/layout/routerView/parent.vue'),
        redirect: '/importserver/preimportserver',
        meta: {
          title: '重点服务',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          // icon: 'iconfont icon-neiqianshujuchucun',
          icon: 'ele-BellFilled',
        },
        children: [
          {
            path: '/importserver/preimportserver',
            name: 'importserver_preimportserver',
            component: () => import('/@/newviews/ImportServe/PreImportServer.vue'),
            meta: {
              title: '重要服务前缀',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-DataAnalysis',
            },
          },
          {
            path: '/importserver/asimportserver',
            name: 'importserver_asimportserver',
            component: () => import('/@/newviews/ImportServe/ASImportServer.vue'),
            meta: {
              title: '重点AS',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-DataAnalysis',
            },
          },
          {
            path: '/importserver/orgimportserver',
            name: 'importserver_orgimportserver',
            component: () => import('/@/newviews/ImportServe/OrgImportServer.vue'),
            meta: {
              title: '重点机构',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-DataAnalysis',
            },
          },
        ]
      },
      {
        path: '/visualization',
        name: 'visualization',
        component: () => import('/@/newviews/visualization/index.vue'),
        meta: {
          title: '数据大屏',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin', 'operator', 'guest'],
          // icon: 'iconfont icon-neiqianshujuchucun',
          icon: 'ele-View',
        },
        children: [
          {
            path: '/visualization/boundary',
            name: 'visualization_boundary',
            component: () => import('/@/newviews/visualization/Boundary/index.vue'),
            meta: {
              title: '国家边界关系',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-Menu',
            },
          },
          {
            path: '/visualization/connectivity',
            name: 'visualization_connectivity',
            component: () => import('/@/newviews/visualization/Connectivity/index.vue'),
            meta: {
              title: '国家联通关系',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-Menu',
            },
          },
          {
            path: '/visualization/safety',
            name: 'visualization_safety',
            component: () => import('/@/newviews/visualization/Safety/index.vue'),
            meta: {
              title: '互联网路由安全',
              isLink: '',
              isHide: false,
              isKeepAlive: false,
              isAffix: false,
              isIframe: false,
              roles: ['admin', 'operator', 'guest'],
              // icon: 'iconfont icon-caidan',
              icon: 'ele-Menu',
            },
          },
        ],
      },
      {
        path: '/user',
        name: 'user',
        component: () => import('/@/newviews/user/index.vue'),
        meta: {
          title: '用户管理',
          isLink: '',
          isHide: false,
          isKeepAlive: false,
          isAffix: false,
          isIframe: false,
          roles: ['admin'],
          // icon: 'iconfont icon-neiqianshujuchucun',
          icon: 'ele-SetUp',
        },
      },
    ],
  },
];

/**
 * 定义404、401界面
 * @link 参考：https://next.router.vuejs.org/zh/guide/essentials/history-mode.html#netlify
 */
export const notFoundAndNoPower = [
  {
    path: '/:path(.*)*',
    name: 'notFound',
    component: () => import('/@/views/error/404.vue'),
    meta: {
      title: 'message.staticRoutes.notFound',
      isHide: true,
    },
  },
  {
    path: '/401',
    name: 'noPower',
    component: () => import('/@/views/error/401.vue'),
    meta: {
      title: 'message.staticRoutes.noPower',
      isHide: true,
    },
  },
];

/**
 * 定义静态路由（默认路由）
 * 此路由不要动，前端添加路由的话，请在 `dynamicRoutes 数组` 中添加
 * @description 前端控制直接改 dynamicRoutes 中的路由，后端控制不需要修改，请求接口路由数据时，会覆盖 dynamicRoutes 第一个顶级 children 的内容（全屏，不包含 layout 中的路由出口）
 * @returns 返回路由菜单数据
 */
export const staticRoutes: Array<RouteRecordRaw> = [
  {
    path: '/login',
    name: 'login',
    component: () => import('/@/views/login/index.vue'),
    meta: {
      title: '登录',
    },
  },
  /**
   * 提示：写在这里的为全屏界面，不建议写在这里
   * 请写在 `dynamicRoutes` 路由数组中
   */
  {
    path: '/visualizingDemo1',
    name: 'visualizingDemo1',
    component: () => import('/@/views/visualizing/demo1.vue'),
    meta: {
      title: 'message.router.visualizingLinkDemo1',
    },
  },
  {
    path: '/visualizingDemo2',
    name: 'visualizingDemo2',
    component: () => import('/@/views/visualizing/demo2.vue'),
    meta: {
      title: 'message.router.visualizingLinkDemo2',
    },
  },
  {
    path: '/BoundaryCharts',
    name: 'BoundaryCharts',
    component: () => import('/@/newviews/visualization/Boundary/ChartsBigScreen/BoundaryCharts.vue'),
    meta: {
      title: '国家边界关系拓扑图详情',
    },
  },
];
