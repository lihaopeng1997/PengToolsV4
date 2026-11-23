/**
 * 保留兼容导出：已关闭按钮点击扩散圆圈水波纹，避免点击主按钮/工具按钮时
 * 出现类似加载动效的一闪而过，交互反馈统一由克制的 hover / active 状态承担。
 */
export function installWebRipples(): () => void {
  return () => {}
}
