/**
 * ECharts 通用工具 Composable
 *
 * 提供 ECharts 实例的自动 resize 监听和组件卸载时的 dispose 清理，
 * 解决多个页面中 ECharts 实例未正确管理导致的内存泄漏和窗口缩放问题。
 *
 * 使用方式:
 *   const { chartRef, initChart } = useECharts();
 *   // 在 onMounted 中调用 initChart(el, option) 初始化图表
 */
import { ref, onUnmounted, type Ref } from 'vue';
import * as echarts from 'echarts';

interface ChartInstance {
  chart: echarts.ECharts | null;
  el: HTMLElement;
  resizeHandler: () => void;
}

/**
 * ECharts 管理 composable
 * - 自动监听窗口 resize 并调用 chart.resize()
 * - 组件卸载时自动 dispose 所有图表实例
 */
export function useECharts() {
  const instances: ChartInstance[] = [];

  onUnmounted(() => {
    instances.forEach(({ chart, resizeHandler }) => {
      if (chart) {
        chart.dispose();
      }
      window.removeEventListener('resize', resizeHandler);
    });
    instances.length = 0;
  });

  /**
   * 初始化一个 ECharts 实例并自动绑定 resize 监听
   * @param el DOM 元素或 ref
   * @param option ECharts 配置项
   * @returns ECharts 实例
   */
  function initChart(el: HTMLElement, option: echarts.EChartsOption): echarts.ECharts {
    const chart = echarts.init(el);
    chart.setOption(option);

    const resizeHandler = () => {
      if (chart && !chart.isDisposed()) {
        chart.resize();
      }
    };
    window.addEventListener('resize', resizeHandler);
    instances.push({ chart, el, resizeHandler });

    return chart;
  }

  /**
   * 批量初始化多个 ECharts 实例
   * @param configs 图表配置数组
   */
  function initCharts(configs: Array<{ el: HTMLElement; option: echarts.EChartsOption }>): echarts.ECharts[] {
    return configs.map(({ el, option }) => initChart(el, option));
  }

  return { initChart, initCharts };
}

export default useECharts;
