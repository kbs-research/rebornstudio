/**
 * 쿠키 세션에서 access 토큰의 보관·복구와 탭 사이 refresh 조율을 본다.
 *
 * refresh 토큰은 HttpOnly 쿠키라 화면이 읽지 않고, access 토큰은 메모리에만 둔다.
 * 새로고침은 쿠키 refresh로 세션을 복구하고, 옛 화면이 저장소에 남긴 토큰은 한 번 교환한 뒤 지운다.
 * 탭들은 쿠키 하나를 같이 쓰므로 동시에 갱신하면 refresh는 한 번만 나가고 다른 탭은 그 결과를 받는다.
 * 한 탭이 로그아웃하면 다른 탭은 같은 채널로 신호를 받아 토큰을 비우고 로그아웃 요청을 다시 보내지 않는다.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** 탭끼리 같이 쓰는 저장소를 흉내 내는 최소 localStorage. */
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

/** 브라우저 Web Locks의 배타 잠금만 흉내 낸다. `ifAvailable`이면 잡혀 있을 때 null로 콜백을 부른다. */
function fakeLocks() {
  let held = false;
  const queue: (() => void)[] = [];
  return {
    async request<T>(
      _name: string,
      optsOrCb: { ifAvailable?: boolean } | ((lock: unknown) => Promise<T>),
      maybeCb?: (lock: unknown) => Promise<T>,
    ): Promise<T> {
      const opts = typeof optsOrCb === "function" ? {} : optsOrCb;
      const cb = (typeof optsOrCb === "function" ? optsOrCb : maybeCb)!;
      if (opts.ifAvailable && held) return cb(null);
      if (held) await new Promise<void>((r) => queue.push(r));
      held = true;
      try {
        return await cb({});
      } finally {
        const next = queue.shift();
        if (next) next();
        else held = false;
      }
    },
  };
}

const json = (status: number, body: unknown = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/**
 * refresh 쿠키를 교체하는 서버와 브라우저 쿠키 저장소를 흉내 낸다.
 * 요청을 보낼 때의 쿠키가 서버의 현재 쿠키와 다르면 401이다. 그래서 같은 쿠키로 동시에 보내면 진 쪽이 401을 받는다.
 */
function cookieServer() {
  let valid = "c1";
  let jar = "c1";
  let issued = 0;
  const bodies: string[] = [];
  const fetch = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    const sent = jar;
    bodies.push(String(init?.body));
    await new Promise((r) => setTimeout(r, 5));
    if (sent !== valid) return json(401, { detail: "invalid_refresh_token" });
    issued += 1;
    valid = jar = `c${issued + 1}`;
    return json(200, { access_token: `a${issued}`, access_expires_at: "2099-01-01T00:00:00Z" });
  });
  return { fetch, bodies, issued: () => issued };
}

/** 새 탭(모듈 인스턴스 하나)을 연다. 메모리 상태는 탭마다 따로다. */
async function openTab() {
  vi.resetModules();
  return import("./auth");
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("토큰 보관", () => {
  it("로그인과 갱신 뒤에도 브라우저 저장소에 토큰이 남지 않는다", async () => {
    const tab = await openTab();
    tab.setSession("a0");
    vi.stubGlobal("fetch", cookieServer().fetch);
    await expect(tab.refreshSession()).resolves.toEqual({ kind: "ok" });
    expect(tab.getAccessToken()).toBe("a1");
    expect(localStorage.length).toBe(0);
  });

  it("refresh 요청은 쿠키를 싣고 본문에 토큰을 넣지 않는다", async () => {
    const tab = await openTab();
    const server = cookieServer();
    vi.stubGlobal("fetch", server.fetch);
    await tab.refreshSession();
    const init = server.fetch.mock.calls[0][1]!;
    expect(init.credentials).toBe("same-origin");
    expect(server.bodies).toEqual(["{}"]);
  });
});

describe("새로고침 복구", () => {
  it("메모리가 빈 새 탭은 쿠키 refresh로 세션을 복구한다", async () => {
    const tab = await openTab();
    vi.stubGlobal("fetch", cookieServer().fetch);
    expect(tab.isAuthed()).toBe(false);
    await expect(tab.restoreSession()).resolves.toEqual({ kind: "ok" });
    expect(tab.isAuthed()).toBe(true);
    expect(tab.restoreUnavailable()).toBe(false);
  });

  it("옛 화면이 저장소에 남긴 refresh 토큰은 한 번 본문으로 보내 쿠키로 바꾸고 지운다", async () => {
    localStorage.setItem("reborn.access_token", "old-access");
    localStorage.setItem("reborn.refresh_token", "old-refresh");
    const tab = await openTab();
    const server = cookieServer();
    vi.stubGlobal("fetch", server.fetch);
    await expect(tab.restoreSession()).resolves.toEqual({ kind: "ok" });
    expect(localStorage.length).toBe(0);
    await tab.refreshSession();
    expect(server.bodies).toEqual([JSON.stringify({ refresh_token: "old-refresh" }), "{}"]);
  });

  it("옛 토큰이 거절되면 지우고 로그인 전 상태로 남는다", async () => {
    localStorage.setItem("reborn.refresh_token", "dead");
    const tab = await openTab();
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "invalid_refresh_token" })));
    await expect(tab.restoreSession()).resolves.toEqual({ kind: "rejected" });
    expect(localStorage.length).toBe(0);
    expect(tab.isAuthed()).toBe(false);
    expect(tab.restoreUnavailable()).toBe(false);
  });

  it("복구가 일시 오류면 다시 시도하고 끝내 실패하면 연결 실패로 남긴다", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    const tab = await openTab();
    const fetch = vi.fn(async () => json(503, { detail: "internal_error" }));
    vi.stubGlobal("fetch", fetch);
    const done = tab.restoreSession();
    await vi.runAllTimersAsync();
    await expect(done).resolves.toMatchObject({ kind: "unavailable" });
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(tab.restoreUnavailable()).toBe(true);
  });

  it("복구가 요청 한도(429)면 곧바로 다시 보내지 않고 서버가 준 대기 시간을 남긴다", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    const tab = await openTab();
    const fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "rate_limit_exceeded:refresh (max 300/min)" }), {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "23" },
        }),
    );
    vi.stubGlobal("fetch", fetch);
    const done = tab.restoreSession();
    await vi.runAllTimersAsync();
    await expect(done).resolves.toMatchObject({ kind: "unavailable" });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(tab.restoreUnavailable()).toBe(true);
    expect(tab.restoreRetryAfter()).toBe(23);
  });

  it("요청 한도에 대기 시간이 없으면 1분을 기다리고, 다른 일시 오류는 대기 시간이 없다", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    const limited = await openTab();
    vi.stubGlobal("fetch", vi.fn(async () => json(429, { detail: "rate_limit_exceeded:refresh" })));
    const done = limited.restoreSession();
    await vi.runAllTimersAsync();
    await done;
    expect(limited.restoreRetryAfter()).toBe(60);

    const down = await openTab();
    vi.stubGlobal("fetch", vi.fn(async () => json(503, { detail: "internal_error" })));
    const failed = down.restoreSession();
    await vi.runAllTimersAsync();
    await failed;
    expect(down.restoreUnavailable()).toBe(true);
    expect(down.restoreRetryAfter()).toBeNull();
  });

  it("복구 중 일시 오류 뒤 성공하면 세션이 이어진다", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    const tab = await openTab();
    const replies = [json(502), json(200, { access_token: "a9" })];
    vi.stubGlobal("fetch", vi.fn(async () => replies.shift()!));
    const done = tab.restoreSession();
    await vi.runAllTimersAsync();
    await expect(done).resolves.toEqual({ kind: "ok" });
    expect(tab.getAccessToken()).toBe("a9");
  });
});

describe("여러 탭", () => {
  it("두 탭이 동시에 갱신하면 refresh는 한 번이고 두 탭이 같은 토큰을 쓴다", async () => {
    vi.stubGlobal("navigator", { locks: fakeLocks() });
    const server = cookieServer();
    vi.stubGlobal("fetch", server.fetch);
    const a = await openTab();
    const b = await openTab();
    a.setSession("old");
    b.setSession("old");
    const [ra, rb] = await Promise.all([a.refreshSession(), b.refreshSession()]);
    expect([ra, rb]).toEqual([{ kind: "ok" }, { kind: "ok" }]);
    expect(server.fetch).toHaveBeenCalledTimes(1);
    expect(b.getAccessToken()).toBe(a.getAccessToken());
  });

  it("다른 탭의 토큰이 오지 않아도 바뀐 쿠키로 직접 갱신해 로그아웃되지 않는다", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    vi.stubGlobal("navigator", { locks: fakeLocks() });
    const server = cookieServer();
    vi.stubGlobal("fetch", server.fetch);
    const a = await openTab();
    const b = await openTab();
    const [ra, rb] = await Promise.all([a.refreshSession(), b.refreshSession()]);
    expect([ra, rb]).toEqual([{ kind: "ok" }, { kind: "ok" }]);
    expect(server.issued()).toBe(2);
  });

  it("Web Locks가 없을 때 401을 기다리는 사이 다른 탭의 토큰이 오면 성공으로 본다", async () => {
    const tab = await openTab();
    tab.setSession("old");
    const other = new BroadcastChannel("reborn.auth");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        other.postMessage({ type: "access", access: "from-other-tab" });
        await new Promise((r) => setTimeout(r, 20));
        return json(401, { detail: "invalid_refresh_token" });
      }),
    );
    await expect(tab.refreshSession()).resolves.toEqual({ kind: "ok" });
    expect(tab.getAccessToken()).toBe("from-other-tab");
    other.close();
  });

  it("거절된 refresh는 다른 탭의 토큰 없이 rejected다", async () => {
    const tab = await openTab();
    tab.setSession("old");
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "invalid_refresh_token" })));
    await expect(tab.refreshSession()).resolves.toEqual({ kind: "rejected" });
  });
});

describe("다른 탭의 로그아웃", () => {
  it("채널 메시지를 토큰 공유와 로그아웃으로 가르고 나머지는 버린다", async () => {
    const { readSessionMessage } = await openTab();
    expect(readSessionMessage({ type: "access", access: "a1" })).toEqual({ type: "access", access: "a1" });
    expect(readSessionMessage({ type: "signed-out" })).toEqual({ type: "signed-out" });
    expect(readSessionMessage({ type: "access", access: 1 })).toBeNull();
    expect(readSessionMessage({ type: "logout" })).toBeNull();
    expect(readSessionMessage(null)).toBeNull();
    expect(readSessionMessage("signed-out")).toBeNull();
  });

  it("한 탭이 로그아웃하면 다른 탭은 토큰을 비우고 알림을 받으며 로그아웃 요청을 다시 보내지 않는다", async () => {
    const fetch = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    const b = await openTab();
    b.setSession("b-access");
    const heard = vi.fn();
    const stop = b.onSignedOutElsewhere(heard);
    const a = await openTab();
    a.setSession("a-access");
    a.announceSignOut();
    await vi.waitFor(() => expect(heard).toHaveBeenCalledTimes(1));
    expect(b.getAccessToken()).toBeNull();
    expect(b.isAuthed()).toBe(false);
    expect(a.getAccessToken()).toBe("a-access");
    expect(fetch).not.toHaveBeenCalled();
    expect(b.sessionEndedBySignOut()).toBe(true);
    stop();
  });

  it("로그아웃으로 끝난 세션만 표시하고 새 세션을 받으면 표시를 지운다", async () => {
    const tab = await openTab();
    tab.setSession("first");
    tab.clearSession();
    expect(tab.sessionEndedBySignOut()).toBe(false);
    tab.setSession("second");
    tab.clearSession("signed-out");
    expect(tab.sessionEndedBySignOut()).toBe(true);
    tab.setSession("third");
    expect(tab.sessionEndedBySignOut()).toBe(false);
  });

  it("구독을 해제한 뒤에는 로그아웃 알림을 받지 않는다", async () => {
    const b = await openTab();
    b.setSession("b-access");
    const heard = vi.fn();
    b.onSignedOutElsewhere(heard)();
    const other = new BroadcastChannel("reborn.auth");
    other.postMessage({ type: "signed-out" });
    await vi.waitFor(() => expect(b.getAccessToken()).toBeNull());
    expect(heard).not.toHaveBeenCalled();
    other.close();
  });
});
