const localSnapshot = new URL('../data/snapshot.enc.json', import.meta.url);
const repositorySnapshot = 'https://raw.githubusercontent.com/preplic/yxGPUmonitor/main/site/data/snapshot.enc.json';

export async function fetchSnapshot() {
  const urls = localSnapshot.hostname === 'preplic.github.io'
    ? [new URL(repositorySnapshot), localSnapshot] : [localSnapshot];
  for (const url of urls) {
    try {
      const fresh = new URL(url);
      fresh.searchParams.set('t', String(Date.now()));
      const response = await fetch(fresh, {
        cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(15000)
      });
      if (response.ok) return await response.json();
    } catch {
      // Keep the deployed encrypted copy available during a temporary source outage.
    }
  }
  throw new Error('暂时无法获取监控快照，请稍后重试');
}
