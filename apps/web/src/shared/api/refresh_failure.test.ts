/**
 * refresh 실패의 종류에 따라 세션을 지우거나 두는지 본다.
 *
 * refresh가 401이면 refresh 쿠키가 무효이거나 세션 절대 수명이 지난 것이라 세션을 지우고 만료를 알린다.
 * 503·429·연결 실패는 서버 상태 때문에 생긴 일시 오류다. 세션을 지우면 일하던 사람이 비밀번호를 다시 쳐야 하므로
 * 메모리의 access 토큰을 두고 원래 요청만 실패로 돌린다. 다음 요청이 401을 받으면 다시 갱신한다.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IDLE_LIMIT_MS, accessLapsed, getAccessToken, setSession } from "./auth";
import { api } from "./client";

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

/** exp(초)만 담은 서명 없는 access 토큰. 화면은 exp만 읽는다. */
function accessWithExp(expSec: number): string {
  const body = btoa(JSON.stringify({ exp: expSec })).replace(/=+$/, "");
  return `h.${body}.s`;
}

type Reply = Response | Error;

/**
 * 경로별로 정한 응답을 순서대로 돌려주는 fetch. 응답 대신 Error를 두면 연결 실패처럼 던진다.
 * 부른 경로를 `calls`에 남긴다.
 */
function scriptedFetch(script: Record<string, Reply[]>) {
  const calls: string[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    const queue = script[url];
    const next = queue?.shift();
    if (!next) throw new Error(`예상하지 않은 요청: ${url}`);
    if (next instanceof Error) throw next;
    return next;
  });
  return { fn, calls };
}

const json = (status: number, body: unknown = {}, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

describe("refresh 실패와 세션", () => {
  let expired: number;

  beforeEach(() => {
    vi.stubGlobal("localStorage", memoryStorage());
    vi.stubGlobal("window", new EventTarget());
    expired = 0;
    window.addEventListener("reborn:session-expired", () => {
      expired += 1;
    });
    setSession("a1");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("refresh가 401이면 세션을 지우고 만료를 알린다", async () => {
    const { fn } = scriptedFetch({
      "/api/projects": [json(401, { detail: "invalid_token" })],
      "/api/auth/refresh": [json(401, { detail: "invalid_refresh_token" })],
    });
    vi.stubGlobal("fetch", fn);
    await expect(api("/projects")).rejects.toMatchObject({ status: 401, detail: "unauthorized" });
    expect(getAccessToken()).toBeNull();
    expect(expired).toBe(1);
  });

  it("refresh가 503이면 토큰을 두고 원래 요청을 서버 오류로 실패시킨다", async () => {
    const { fn } = scriptedFetch({
      "/api/projects": [json(401, { detail: "invalid_token" })],
      "/api/auth/refresh": [json(503, { detail: "internal_error" })],
    });
    vi.stubGlobal("fetch", fn);
    await expect(api("/projects")).rejects.toMatchObject({ status: 503, detail: "internal_error" });
    expect(getAccessToken()).toBe("a1");
    expect(expired).toBe(0);
  });

  it("refresh가 연결 실패면 토큰을 두고 network_error로 실패시킨다", async () => {
    const { fn } = scriptedFetch({
      "/api/projects": [json(401, { detail: "invalid_token" })],
      "/api/auth/refresh": [new TypeError("Failed to fetch")],
    });
    vi.stubGlobal("fetch", fn);
    await expect(api("/projects")).rejects.toMatchObject({ status: 0, detail: "network_error" });
    expect(getAccessToken()).toBe("a1");
    expect(expired).toBe(0);
  });

  it("refresh가 429면 토큰을 두고 대기 시간을 싣는다", async () => {
    const { fn } = scriptedFetch({
      "/api/projects": [json(401, { detail: "invalid_token" })],
      "/api/auth/refresh": [json(429, { detail: "rate_limit_exceeded:refresh" }, { "Retry-After": "30" })],
    });
    vi.stubGlobal("fetch", fn);
    await expect(api("/projects")).rejects.toMatchObject({ status: 429, retryAfter: 30 });
    expect(getAccessToken()).toBe("a1");
    expect(expired).toBe(0);
  });

  it("일시 오류 다음 요청은 다시 갱신해 성공한다", async () => {
    const { fn } = scriptedFetch({
      "/api/projects": [json(401), json(401), json(200, [{ id: 1 }])],
      "/api/auth/refresh": [
        json(502),
        json(200, { access_token: "a2" }),
      ],
    });
    vi.stubGlobal("fetch", fn);
    await expect(api("/projects")).rejects.toMatchObject({ status: 502 });
    await expect(api("/projects")).resolves.toEqual([{ id: 1 }]);
    expect(getAccessToken()).toBe("a2");
    expect(expired).toBe(0);
  });

  it("동시에 기다린 요청은 refresh 한 번의 일시 오류를 함께 받고 토큰은 남는다", async () => {
    const { fn, calls } = scriptedFetch({
      "/api/projects": [json(401)],
      "/api/me": [json(401)],
      "/api/auth/refresh": [json(503)],
    });
    vi.stubGlobal("fetch", fn);
    const results = await Promise.allSettled([api("/projects"), api("/me")]);
    expect(results.map((r) => r.status)).toEqual(["rejected", "rejected"]);
    for (const r of results) {
      expect((r as PromiseRejectedResult).reason).toMatchObject({ status: 503 });
    }
    expect(calls.filter((c) => c === "/api/auth/refresh")).toHaveLength(1);
    expect(getAccessToken()).toBe("a1");
    expect(expired).toBe(0);
  });

  it("요청 자체가 연결 실패면 network_error를 던진다", async () => {
    vi.stubGlobal("fetch", scriptedFetch({ "/api/projects": [new TypeError("Failed to fetch")] }).fn);
    await expect(api("/projects")).rejects.toMatchObject({ status: 0, detail: "network_error" });
  });
});

describe("accessLapsed", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", memoryStorage());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const past = () => Math.floor(Date.now() / 1000) - 60;

  it("만료 시각이 남았으면 갱신하지 않고 거짓이다", async () => {
    setSession(accessWithExp(past() + 3600));
    const { fn } = scriptedFetch({});
    vi.stubGlobal("fetch", fn);
    await expect(accessLapsed()).resolves.toBe(false);
    expect(fn).not.toHaveBeenCalled();
  });

  it("만료 뒤 refresh가 일시 오류면 세션이 끝나지 않은 것으로 본다", async () => {
    setSession(accessWithExp(past()));
    vi.stubGlobal("fetch", scriptedFetch({ "/api/auth/refresh": [json(503)] }).fn);
    await expect(accessLapsed()).resolves.toBe(false);
  });

  it("만료 뒤 refresh가 401이면 세션이 끝났다", async () => {
    setSession(accessWithExp(past()));
    vi.stubGlobal("fetch", scriptedFetch({ "/api/auth/refresh": [json(401)] }).fn);
    await expect(accessLapsed()).resolves.toBe(true);
  });

  it("만료 뒤 refresh가 성공하면 세션이 이어진다", async () => {
    setSession(accessWithExp(past()));
    const fresh = accessWithExp(past() + 3600);
    vi.stubGlobal(
      "fetch",
      scriptedFetch({ "/api/auth/refresh": [json(200, { access_token: fresh })] }).fn,
    );
    await expect(accessLapsed()).resolves.toBe(false);
    expect(getAccessToken()).toBe(fresh);
  });

  it("만료 뒤 유휴 상태면 갱신하지 않고 세션이 끝났다", async () => {
    setSession(accessWithExp(past()));
    const { fn } = scriptedFetch({});
    vi.stubGlobal("fetch", fn);
    vi.useFakeTimers({ now: Date.now() + IDLE_LIMIT_MS + 60_000, toFake: ["Date"] });
    try {
      await expect(accessLapsed()).resolves.toBe(true);
    } finally {
      vi.useRealTimers();
    }
    expect(fn).not.toHaveBeenCalled();
  });
});
