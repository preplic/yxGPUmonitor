const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const finalErrors = new Set([
  'deployment_failed', 'deployment_perms_error', 'deployment_content_failed',
  'deployment_cancelled', 'deployment_lost', 'errored', 'cancelled'
]);

module.exports = async function deployPages({ core, github, context, artifactId, sleep = delay }) {
  const id = Number(artifactId);
  if (!Number.isSafeInteger(id) || id <= 0) throw new Error('Missing or invalid Pages artifact ID.');
  const oidcToken = await core.getIDToken();
  core.setSecret(oidcToken);

  // Use the uploader's ID directly; artifact listing can temporarily return an empty result.
  const { data: deployment } = await github.request('POST /repos/{owner}/{repo}/pages/deployments', {
    ...context.repo, artifact_id: id, pages_build_version: context.sha, oidc_token: oidcToken
  });
  const deploymentId = deployment.id || deployment.status_url?.split('/').pop() || context.sha;
  const parameters = { ...context.repo, deploymentId };
  let settled = false;
  let errors = 0;
  try {
    for (let attempt = 0; attempt < 90; attempt++) {
      let status;
      try {
        const response = await github.request('GET /repos/{owner}/{repo}/pages/deployments/{deploymentId}', parameters);
        status = response.data.status;
        errors = 0;
      } catch (error) {
        const temporary = error.status === 404 || error.status === 429 || error.status >= 500;
        if (!temporary || ++errors >= 5) throw new Error(`Cannot check Pages deployment (HTTP ${error.status || 'unknown'}).`);
        core.info(`Pages status temporarily unavailable (HTTP ${error.status}); retrying.`);
      }
      if (status === 'succeed') {
        settled = true;
        core.setOutput('status', 'succeed');
        core.setOutput('page_url', deployment.page_url);
        core.info('GitHub Pages deployment succeeded.');
        return;
      }
      if (finalErrors.has(status)) {
        settled = true;
        throw new Error(`GitHub Pages deployment failed: ${status}.`);
      }
      if (status) core.info(`GitHub Pages deployment status: ${status}.`);
      await sleep(5000);
    }
    throw new Error('GitHub Pages deployment timed out after 450 seconds.');
  } finally {
    if (!settled) {
      try {
        await github.request('POST /repos/{owner}/{repo}/pages/deployments/{deploymentId}/cancel', parameters);
      } catch {
        core.warning('Could not cancel the unfinished Pages deployment.');
      }
    }
  }
};
