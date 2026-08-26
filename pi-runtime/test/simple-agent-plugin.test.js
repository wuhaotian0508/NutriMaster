import assert from "node:assert/strict";
import test from "node:test";

import { createAgent } from "../examples/simple-agent-plugin/agent.js";
import calculatorPlugin from "../examples/simple-agent-plugin/calculator-plugin.js";

test("agent loads the calculator plugin and uses its add tool", () => {
  const agent = createAgent([calculatorPlugin]);
  assert.equal(agent.run("请计算 12 + 30"), "计算结果是 42");
});
