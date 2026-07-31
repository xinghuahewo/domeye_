/**
 * Core 的唯一会话实现定义在 server 模块；这里提供稳定的领域入口。
 * 它不再包裹任何兼容 Manager，也不持有编排层依赖。
 */
export {
  CountryOutageCoreSessionManager,
  type CountryOutageCoreSessionManagerOptions,
} from '../server/country-outage-session-manager.js'
