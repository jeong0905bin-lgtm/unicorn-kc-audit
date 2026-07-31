#!/usr/bin/env node
import fs from 'node:fs/promises';
import process from 'node:process';
import { parse } from 'acorn';

const SCRIPT = 'https://front.coupangcdn.com/coupang-store-display/20260324160003_kr/f6ae536.js';
const ENDPOINTS = [
  '/api/v2/store/individualInfo/product',
  '/api/v2/store/individualInfo/products',
];

const outputIndex = process.argv.indexOf('--output');
if (outputIndex < 0 || !process.argv[outputIndex + 1]) {
  throw new Error('Usage: resolve_shop_bundle_binding.mjs --output <path>');
}
const output = process.argv[outputIndex + 1];

const response = await fetch(SCRIPT, {
  headers: {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36',
    'accept-language': 'ko-KR,ko;q=0.9,en-US;q=0.7',
    referer: 'https://shop.coupang.com/A00214628',
  },
});
const text = await response.text();
const ast = parse(text, { ecmaVersion: 'latest', sourceType: 'script', ranges: true });

const functions = new Map();
let nextFunctionId = 1;
function fnId(node) {
  if (!node) return 'program';
  if (!functions.has(node)) functions.set(node, `fn${nextFunctionId++}`);
  return functions.get(node);
}

const bindings = [];
const endpointHits = [];

function isFunction(node) {
  return node && [
    'FunctionDeclaration', 'FunctionExpression', 'ArrowFunctionExpression'
  ].includes(node.type);
}

function walk(node, ancestors = [], fnStack = []) {
  if (!node || typeof node !== 'object') return;
  const nextFnStack = isFunction(node) ? [...fnStack, node] : fnStack;

  if (node.type === 'VariableDeclarator' && node.id?.type === 'Identifier') {
    bindings.push({
      name: node.id.name,
      kind: 'declarator',
      start: node.start,
      end: node.end,
      initStart: node.init?.start ?? null,
      initEnd: node.init?.end ?? null,
      fnStack: [...nextFnStack],
    });
  }
  if (node.type === 'AssignmentExpression' && node.left?.type === 'Identifier') {
    bindings.push({
      name: node.left.name,
      kind: 'assignment',
      start: node.start,
      end: node.end,
      initStart: node.right?.start ?? null,
      initEnd: node.right?.end ?? null,
      fnStack: [...nextFnStack],
    });
  }
  if ((node.type === 'Literal' || node.type === 'StringLiteral') && ENDPOINTS.includes(node.value)) {
    endpointHits.push({
      endpoint: node.value,
      start: node.start,
      end: node.end,
      fnStack: [...nextFnStack],
      ancestors: [...ancestors, node],
    });
  }

  for (const [key, value] of Object.entries(node)) {
    if (['start', 'end', 'range', 'loc'].includes(key)) continue;
    if (Array.isArray(value)) {
      for (const child of value) {
        if (child && typeof child.type === 'string') walk(child, [...ancestors, node], nextFnStack);
      }
    } else if (value && typeof value.type === 'string') {
      walk(value, [...ancestors, node], nextFnStack);
    }
  }
}
walk(ast);

function source(start, end, pad = 0) {
  if (start == null || end == null) return null;
  return text.slice(Math.max(0, start - pad), Math.min(text.length, end + pad));
}

function sharedDepth(bindingStack, hitStack) {
  let depth = 0;
  const max = Math.min(bindingStack.length, hitStack.length);
  while (depth < max && bindingStack[depth] === hitStack[depth]) depth += 1;
  return depth;
}

const result = {
  script: SCRIPT,
  scriptStatus: response.status,
  scriptLength: text.length,
  endpoints: [],
};

for (const hit of endpointHits) {
  const endpointRow = {
    endpoint: hit.endpoint,
    position: hit.start,
    functionStack: hit.fnStack.map(fnId),
    callContext: source(hit.start, hit.end, 2500),
    bindings: {},
  };

  for (const name of ['S', 'P', 'A', 'f', 'X', 'z']) {
    const candidates = bindings
      .filter((b) => b.name === name && b.start < hit.start)
      .map((b) => ({ ...b, depth: sharedDepth(b.fnStack, hit.fnStack) }))
      .filter((b) => b.depth > 0 || b.fnStack.length === 0)
      .sort((a, b) => (b.depth - a.depth) || (b.start - a.start));
    endpointRow.bindings[name] = candidates.slice(0, 8).map((b) => ({
      kind: b.kind,
      start: b.start,
      functionStack: b.fnStack.map(fnId),
      sharedFunctionDepth: b.depth,
      bindingSource: source(b.start, b.end, 1200),
      initializerSource: source(b.initStart, b.initEnd, 0),
    }));
  }
  result.endpoints.push(endpointRow);
}

result.summary = {
  endpointHits: endpointHits.length,
  totalBindings: bindings.length,
  endpointsWithSBinding: result.endpoints.filter((row) => row.bindings.S.length > 0).length,
};

await fs.mkdir(new URL('.', `file://${process.cwd()}/${output}`).pathname, { recursive: true }).catch(() => {});
await fs.mkdir(output.split('/').slice(0, -1).join('/') || '.', { recursive: true });
await fs.writeFile(output, JSON.stringify(result, null, 2), 'utf8');
console.log(JSON.stringify(result.summary, null, 2));
