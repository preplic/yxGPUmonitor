export const SESSION_LIFETIME = 12 * 60 * 60 * 1000;
const DATABASE = 'yxgpu-sessions';
const STORE = 'sessions';
const REFERENCE = 'yxgpu-session';

function reference() {
  try { return sessionStorage.getItem(REFERENCE); } catch { return null; }
}

async function transaction(mode, action) {
  const database = await new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    let abandoned = false;
    const fail = () => { abandoned = true; reject(new Error('Session storage unavailable')); };
    const timer = setTimeout(fail, 2000);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: 'id' });
    request.onsuccess = () => {
      clearTimeout(timer);
      if (abandoned) request.result.close();
      else resolve(request.result);
    };
    request.onerror = request.onblocked = () => {
      clearTimeout(timer);
      fail();
    };
  });
  try {
    return await new Promise((resolve, reject) => {
      const task = database.transaction(STORE, mode);
      const request = action(task.objectStore(STORE));
      task.oncomplete = () => resolve(request?.result);
      task.onerror = task.onabort = () => reject(task.error || new Error('Session storage failed'));
    });
  } finally { database.close(); }
}

export async function clearSession() {
  const id = reference();
  try { sessionStorage.removeItem(REFERENCE); } catch { /* Storage may be disabled. */ }
  if (id) {
    try { await transaction('readwrite', store => store.delete(id)); } catch { /* The tab reference is already removed. */ }
  }
}

export async function loadSession() {
  if (!reference()) return null;
  try {
    const session = await transaction('readonly', store => store.get(reference()));
    if (session && typeof session.user === 'string' && typeof session.salt === 'string' &&
        Number.isFinite(session.expiresAt) && session.expiresAt > Date.now() &&
        session.expiresAt <= Date.now() + SESSION_LIFETIME &&
        session.key instanceof CryptoKey && !session.key.extractable &&
        session.key.algorithm.name === 'AES-GCM' && session.key.algorithm.length === 256 &&
        session.key.usages.length === 1 && session.key.usages[0] === 'decrypt') return session;
  } catch { /* Fall back to manual login if the session cannot be restored. */ }
  await clearSession();
  return null;
}

export async function saveSession(session) {
  const id = crypto.randomUUID();
  const previous = reference();
  try {
    // Structured cloning preserves the CryptoKey's non-extractable property.
    await transaction('readwrite', store => {
      const cursor = store.openCursor();
      cursor.onsuccess = () => {
        const item = cursor.result;
        if (!item) return;
        if (item.value.expiresAt <= Date.now() || item.key === previous) item.delete();
        item.continue();
      };
      return store.put({ ...session, id });
    });
    sessionStorage.setItem(REFERENCE, id);
    return true;
  } catch {
    try { await transaction('readwrite', store => store.delete(id)); } catch { /* Persistence is optional. */ }
    return false;
  }
}
