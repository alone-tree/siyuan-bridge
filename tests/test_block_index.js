"use strict";

const fs = require("fs");
const path = require("path");
const {
  collectDisplayBlockIndexes,
  transactionChangesBlockStructure,
} = require("../siyuan-plugin/block-index.js");

const fixturePath = path.join(__dirname, "fixtures", "display_block_index_cases.json");
const payload = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
const failures = [];

for (const testCase of payload.cases) {
  const actual = collectDisplayBlockIndexes(testCase.root_id, testCase.children);
  const expected = JSON.stringify(testCase.expected);
  const got = JSON.stringify(actual);
  if (expected !== got) {
    failures.push(`${testCase.name}\n  expected: ${expected}\n  actual:   ${got}`);
  }
}

if (!transactionChangesBlockStructure({cmd: "transactions", data: [{doOperations: [{action: "insert"}]}]})) {
  failures.push("insert should recompute");
}
if (!transactionChangesBlockStructure({cmd: "transactions", data: [{doOperations: [{action: "delete"}]}]})) {
  failures.push("delete should recompute");
}
if (!transactionChangesBlockStructure({cmd: "transactions", data: [{doOperations: [{action: "move"}]}]})) {
  failures.push("move should recompute");
}
if (transactionChangesBlockStructure({cmd: "transactions", data: [{doOperations: [{action: "update"}]}]})) {
  failures.push("update should not recompute");
}
if (transactionChangesBlockStructure({cmd: "setAttrs", data: [{doOperations: [{action: "insert"}]}]})) {
  failures.push("non-transaction cmd should not recompute");
}

if (failures.length) {
  process.stderr.write(failures.join("\n") + "\n");
  process.exit(1);
}

process.stdout.write(`ok ${payload.cases.length} fixture cases plus transaction filters\n`);
