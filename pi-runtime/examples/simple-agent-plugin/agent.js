import calculatorPlugin from "./calculator-plugin.js";

// 这是最小的 Agent 宿主：插件只知道 registerTool 接口，
// Agent 负责加载插件、理解输入、选择并执行工具。
export function createAgent(plugins = []) {
  const tools = new Map();
  const api = {
    registerTool(tool) {
      tools.set(tool.name, tool);
      console.log(`[插件加载] ${tool.name}: ${tool.description}`);
    },
  };

  for (const plugin of plugins) plugin(api);

  return {
    run(input) {
      console.log(`[用户] ${input}`);
      const match = input.match(/(-?\d+(?:\.\d+)?)\s*\+\s*(-?\d+(?:\.\d+)?)/);

      if (!match) {
        const answer = "我目前只会处理形如 12 + 30 的加法。";
        console.log(`[Agent] ${answer}`);
        return answer;
      }

      const args = { a: Number(match[1]), b: Number(match[2]) };
      console.log(`[Agent 决策] 调用工具 add，参数 ${JSON.stringify(args)}`);
      const result = tools.get("add").execute(args);
      const answer = `计算结果是 ${result}`;
      console.log(`[插件返回] ${result}`);
      console.log(`[Agent] ${answer}`);
      return answer;
    },
  };
}

if (process.argv[1] === new URL(import.meta.url).pathname) {
  const input = process.argv.slice(2).join(" ") || "请帮我算 12 + 30";
  createAgent([calculatorPlugin]).run(input);
}
