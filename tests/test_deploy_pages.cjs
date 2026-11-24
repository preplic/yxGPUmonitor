const assert = require('node:assert/strict');
const { test } = require('node:test');
const deployPages = require('../.github/scripts/deploy-pages.cjs');

function fixture(statuses) {
  const calls = [], outputs = {}, secrets = [], messages = [];
  const core = {
    getIDToken: async () => 'test-oidc-token',
    setSecret: value => secrets.push(value),
    setOutput: (key, value) => { outputs[key] = value; },
    info: value => messages.push(value), warning: value => messages.push(value)
  };
  const github = { request: async (route, parameters) => {
    calls.push({ route, parameters });
    if (route.endsWith('/cancel')) return { data: {} };
    if (route.startsWith('POST')) return { data: { id: 12, page_url: 'https://example.github.io/monitor/' } };
    const status = statuses.length > 1 ? statuses.shift() : statuses[0];
    if (status instanceof Error) throw status;
    return { data: { status } };
  } };
  return { core, github, context: { repo: { owner: 'example', repo: 'monitor' }, sha: 'test-commit' },
    artifactId: '42', sleep: async () => {}, calls, outputs, secrets, messages };
}

test('deploys the uploaded ID without listing artifacts and waits for success', async () => {
  const state = fixture(['queued', 'deployment_in_progress', 'succeed']);
  await deployPages(state);
  assert.deepEqual(state.calls[0].parameters, { owner: 'example', repo: 'monitor',
    artifact_id: 42, pages_build_version: 'test-commit', oidc_token: 'test-oidc-token' });
  assert.ok(state.calls.every(call => !call.route.includes('artifacts')));
  assert.deepEqual(state.outputs, { status: 'succeed', page_url: 'https://example.github.io/monitor/' });
  assert.deepEqual(state.secrets, ['test-oidc-token']);
  assert.ok(!JSON.stringify(state.messages).includes('test-oidc-token'));
});

test('rejects missing or invalid upload IDs before requesting credentials', async () => {
  for (const artifactId of [undefined, '', 'not-an-id', '-1', '1.5']) {
    const state = fixture(['succeed']);
    await assert.rejects(deployPages({ ...state, artifactId }), /artifact ID/);
    assert.equal(state.calls.length, 0);
    assert.deepEqual(state.secrets, []);
  }
});

test('retries a transient status error', async () => {
  const error = Object.assign(new Error('temporary'), { status: 503 });
  const state = fixture([error, 'succeed']);
  await deployPages(state);
  assert.equal(state.outputs.status, 'succeed');
  assert.ok(!state.calls.some(call => call.route.endsWith('/cancel')));
});

test('reports a failed deployment without claiming success', async () => {
  const state = fixture(['deployment_content_failed']);
  await assert.rejects(deployPages(state), /deployment_content_failed/);
  assert.deepEqual(state.outputs, {});
});

test('retries a temporary Pages backend failure using the same uploaded artifact', async () => {
  const state = fixture(['deployment_failed', 'succeed']);
  const waits = [];
  state.sleep = async milliseconds => { waits.push(milliseconds); };
  await deployPages(state);
  const creates = state.calls.filter(call => call.route.endsWith('/deployments') && call.route.startsWith('POST'));
  assert.equal(creates.length, 2);
  assert.ok(creates.every(call => call.parameters.artifact_id === 42));
  assert.deepEqual(waits, [30000]);
  assert.equal(state.outputs.status, 'succeed');
});

test('stops after three failed Pages deployment attempts', async () => {
  const state = fixture(['deployment_failed']);
  await assert.rejects(deployPages(state), /deployment_failed/);
  assert.equal(state.calls.filter(call => call.route.endsWith('/deployments') && call.route.startsWith('POST')).length, 3);
  assert.deepEqual(state.outputs, {});
});

test('cancels a deployment that never finishes', async () => {
  const state = fixture(['deployment_in_progress']);
  await assert.rejects(deployPages(state), /timed out/);
  assert.ok(state.calls.at(-1).route.endsWith('/cancel'));
  assert.equal(state.calls.filter(call => call.route.startsWith('GET')).length, 90);
  assert.deepEqual(state.outputs, {});
});

test('bounds repeated status failures and cancels the pending deployment', async () => {
  const state = fixture([Object.assign(new Error('temporary'), { status: 500 })]);
  await assert.rejects(deployPages(state), /HTTP 500/);
  assert.equal(state.calls.filter(call => call.route.startsWith('GET')).length, 5);
  assert.ok(state.calls.at(-1).route.endsWith('/cancel'));
  assert.deepEqual(state.outputs, {});
});
