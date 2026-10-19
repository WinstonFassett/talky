// Daemon convo API (WIN-535). "Sessions" in the UI, convos on the wire.

export type ConvoStatus = 'idle' | 'working' | 'failed';

export type ConvoRow = {
  id: string;
  backend: string;
  profile: string;
  started_at: number;
  last_active_at: number;
  title: string;
  native_session_id: string | null;
  pinned: boolean;
  archived: boolean;
  snippet: string | null;
};

export type CurrentConvo = { id: string; status: ConvoStatus; live: boolean } | null;

export type ConvoTurn = { index: number; role: 'user' | 'assistant'; content: string; timestamp: number };

export type ConvoPage = { convo: ConvoRow; turns: ConvoTurn[]; start: number; total: number };

export type ConvoChangedEvent = {
  id: string | null;
  reason: 'created' | 'turn' | 'status' | 'current' | 'updated' | 'deleted';
  status: ConvoStatus;
  current: string | null;
};

export type ProfileInfo = { name: string; label: string; active: boolean };

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    let code: string | undefined;
    try {
      const body = await res.json();
      msg = body.error ?? msg;
      code = body.code;
    } catch { /* not json */ }
    throw new ApiError(msg, res.status, code);
  }
  return res.json() as Promise<T>;
}

const send = (url: string, method: string, body?: unknown) =>
  fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const convosApi = {
  list: (q: string, archived: boolean) =>
    fetch(`/api/convos?${new URLSearchParams({ q, archived: archived ? '1' : '0' })}`).then((r) =>
      json<{ convos: ConvoRow[]; current: CurrentConvo }>(r),
    ),
  get: (id: string, opts: { before?: number; limit?: number } = {}) => {
    const p = new URLSearchParams({ limit: String(opts.limit ?? 100) });
    if (opts.before !== undefined) p.set('before', String(opts.before));
    return fetch(`/api/convos/${id}?${p}`).then((r) => json<ConvoPage>(r));
  },
  patch: (id: string, fields: Partial<Pick<ConvoRow, 'title' | 'pinned' | 'archived'>>) =>
    send(`/api/convos/${id}`, 'PATCH', fields).then((r) => json<{ convo: ConvoRow }>(r)),
  remove: (id: string) => send(`/api/convos/${id}`, 'DELETE').then((r) => json<unknown>(r)),
  resume: (id: string, profile?: string) =>
    send('/api/convos/resume', 'POST', { convo_id: id, profile: profile ?? null }).then((r) =>
      json<{ convo_id: string; profile: string }>(r),
    ),
  startNew: () => send('/api/convos/new', 'POST', {}).then((r) => json<unknown>(r)),
  profiles: () => fetch('/api/profiles').then((r) => json<{ profiles: ProfileInfo[] }>(r)),
  switchProfile: (profile: string) =>
    send('/api/profiles/switch', 'POST', { profile }).then((r) => json<unknown>(r)),
};

// T3 Code threads (WIN-564): read-only list + attach (binds a Talky session to a T3 thread).
export type T3ThreadStatus = 'running' | 'idle' | 'completed' | 'failed';

export type T3Thread = {
  id: string;
  short: string;
  title: string;
  project: string;
  project_id: string | null;
  status: T3ThreadStatus;
  last_activity: string;
  model: string;
  pinned: boolean;
  convo_id: string | null;
};

export type T3Project = {
  id: string;
  title: string;
  root: string;
  root_name: string;
  thread_count: number;
  running: number;
  last_activity: string | null;
};

export type T3Status = {
  state: 'connected' | 'needs_connect';
  code: null | 'no_cli' | 't3_down' | 'denied' | 'expired';
  detail: string;
  session: string | null;
  expires_at: string | null;
  scopes: string[];
  base_url: string;
};

export const t3Api = {
  status: () => fetch('/api/t3/status').then((r) => json<T3Status>(r)),
  connect: () => send('/api/t3/connect', 'POST').then((r) => json<T3Status>(r)),
  disconnect: () => send('/api/t3/disconnect', 'POST').then((r) => json<T3Status>(r)),
  projects: async (etag?: string): Promise<{ notModified: true } | { notModified: false; etag: string | null; projects: T3Project[] }> => {
    const r = await fetch('/api/t3/projects', { headers: etag ? { 'If-None-Match': etag } : undefined });
    if (r.status === 304) return { notModified: true };
    const body = await json<{ projects: T3Project[] }>(r);
    return { notModified: false, etag: r.headers.get('ETag'), projects: body.projects };
  },
  threads: (opts: { project?: string; cursor?: string | null; limit?: number } = {}) => {
    const p = new URLSearchParams({ limit: String(opts.limit ?? 20) });
    if (opts.project) p.set('project', opts.project);
    if (opts.cursor) p.set('cursor', opts.cursor);
    return fetch(`/api/t3/threads?${p}`).then((r) =>
      json<{ threads: T3Thread[]; total: number; next_cursor: string | null }>(r),
    );
  },
  attach: (threadId: string) =>
    send('/api/t3/attach', 'POST', { thread_id: threadId }).then((r) =>
      json<{ convo_id: string; created: boolean; thread: unknown }>(r),
    ),
};
