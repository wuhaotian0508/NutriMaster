// 一个最小插件：向宿主注册名为 add 的工具。
export default function calculatorPlugin(api) {
  api.registerTool({
    name: "add",
    description: "计算两个数字的和",
    execute({ a, b }) {
      return Number(a) + Number(b);
    },
  });
}
