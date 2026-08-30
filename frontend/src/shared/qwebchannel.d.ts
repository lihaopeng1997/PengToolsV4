/**
 * Qt WebEngine 注入的全局声明（qrc:///qtwebchannel/qwebchannel.js）。
 *
 * 只声明当前真正需要的 globals：
 * - window.qt.webChannelTransport ：QWebChannel 连接通道
 * - QWebChannel                   ：官方 qwebchannel.js 提供的构造函数
 * 不引入任何第三方 QWebChannel npm package。
 */

interface Window {
  /** Qt WebChannel 桥接对象；普通浏览器中不存在。 */
  qt?: {
    webChannelTransport: unknown
  }
}

/** qrc:///qtwebchannel/qwebchannel.js 的全局构造类（仅 bridge 场景所需的最小形态）。 */
declare class QWebChannel {
  constructor(
    transport: unknown,
    callback: (channel: { objects: Record<string, unknown> }) => void,
  )
}
