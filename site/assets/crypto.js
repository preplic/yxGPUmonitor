const AAD = new TextEncoder().encode('yxGPUmonitor:v1');

export function bytes(value) {
  return Uint8Array.from(atob(value), character => character.charCodeAt(0));
}

export function validateEnvelope(value) {
  if (value?.version !== 1 || value.algorithm !== 'AES-256-GCM' ||
      value.kdf !== 'PBKDF2-SHA256' || value.iterations !== 600000 || value.compression !== 'gzip' ||
      typeof value.ciphertext !== 'string' || value.ciphertext.length > 12000000 ||
      bytes(value.salt).length !== 32 || bytes(value.iv).length !== 12) {
    throw new Error('不支持的快照格式');
  }
}

export async function deriveKey(envelope, password) {
  validateEnvelope(envelope);
  if (!globalThis.crypto?.subtle) throw new Error('请通过 HTTPS 或 localhost 打开控制台');
  const material = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({ name: 'PBKDF2', hash: 'SHA-256', salt: bytes(envelope.salt),
    iterations: envelope.iterations }, material, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
}

export async function decrypt(envelope, key) {
  validateEnvelope(envelope);
  const compressed = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: bytes(envelope.iv),
    additionalData: AAD, tagLength: 128 }, key, bytes(envelope.ciphertext));
  const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip'));
  const value = await new Response(stream).json();
  if (value.schema !== 1 || !Array.isArray(value.allowedUsers) || !Array.isArray(value.history) ||
      !Array.isArray(value.node?.gpus) || !Array.isArray(value.events)) throw new Error('监控快照格式错误');
  return value;
}
