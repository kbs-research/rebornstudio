/**
 * 세션 토큰과 세션 도우미 함수.
 *
 * refresh 토큰은 서버가 HttpOnly 쿠키(`Path=/api/auth`)로 주고받으므로 화면 코드가 읽지 않는다.
 * access 토큰은 이 모듈의 메모리에만 둔다. 브라우저 저장소에 두면 XSS가 생겼을 때 토큰이 바로 나간다.
 * 새로고침과 새 탭은 앱 시작 때 쿠키로 refresh를 한 번 불러 access 토큰을 받는다(`restoreSession`).
 */
import { type ApiError, networkError, responseError } from "./api_error";

/** 이전 버전 화면이 토큰을 브라우저 저장소에 두던 키. 남아 있으면 한 번 교환하고 지운다. */
const LEGACY_ACCESS_KEY = "reborn.access_token";
const LEGACY_REFRESH_KEY = "reborn.refresh_token";

/** 탭끼리 refresh를 한 번에 하나만 보내도록 잡는 Web Locks 이름. */
const REFRESH_LOCK = "reborn.auth.refresh";

/** 갱신한 탭이 새 access 토큰을, 로그아웃한 탭이 로그아웃 신호를 다른 탭에 보내는 BroadcastChannel 이름. */
const SESSION_CHANNEL = "reborn.auth";

/** 다른 탭의 갱신이 끝난 뒤 그 탭이 보낸 토큰을 기다리는 최대 시간. 지나면 이 탭이 직접 갱신한다. */
const SHARE_WAIT_MS = 1_000;

/** 앱 시작 때 refresh가 일시 오류면 다시 시도하기 전에 기다리는 시간. 원소 수가 재시도 횟수다. */
const RESTORE_RETRY_MS = [500, 1_500];

/** 요청 한도(429)에 `Retry-After`가 없을 때 다시 시도하기 전에 기다리는 초. 서버 한도의 창 길이와 같다. */
const RATE_LIMIT_WAIT_S = 60;

let accessToken: string | null = null;

/* 사용자가 이 탭이나 다른 탭에서 로그아웃해 세션이 끝났으면 참. 새 access 토큰을 받으면 거짓으로 돌아간다. */
let endedBySignOut = false;

/* access 토큰을 바꾼 횟수. refresh를 기다리는 동안 다른 탭이나 로그인이 토큰을 바꿨는지 가린다. */
let generation = 0;
const generationWaiters = new Set<() => void>();

/** 저장소 접근이 막힌 환경(사생활 보호 창 등)에서도 동작하도록 감싼 localStorage 읽기. */
function readStorage(key: string): string | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** 브라우저 저장소에 남은 옛 토큰을 지운다. */
function dropLegacyTokens(): void {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.removeItem(LEGACY_ACCESS_KEY);
    localStorage.removeItem(LEGACY_REFRESH_KEY);
  } catch {
    /* 저장소가 막혔으면 지울 것도 없다. */
  }
}

/** 새 access 토큰을 메모리에 두고 기다리던 탭 간 공유 대기를 깨운다. */
function adopt(access: string): void {
  accessToken = access;
  endedBySignOut = false;
  generation += 1;
  for (const wake of generationWaiters) wake();
  generationWaiters.clear();
}

/**
 * 탭 사이 세션 채널의 메시지.
 *
 * `access`는 갱신한 탭이 나누는 새 access 토큰이고, `signed-out`은 사용자가 한 탭에서 로그아웃했다는 신호다.
 */
export type SessionMessage = { type: "access"; access: string } | { type: "signed-out" };

/** 채널로 받은 값을 세션 메시지로 읽는다. 모양이 맞지 않으면 null이라 받은 탭은 아무것도 하지 않는다. */
export function readSessionMessage(data: unknown): SessionMessage | null {
  if (typeof data !== "object" || data === null) return null;
  const m = data as { type?: unknown; access?: unknown };
  if (m.type === "access" && typeof m.access === "string") return { type: "access", access: m.access };
  if (m.type === "signed-out") return { type: "signed-out" };
  return null;
}

/* 다른 탭의 로그아웃 신호를 받을 함수들. 사용자 상태와 화면 이동을 맡는 쪽이 등록한다. */
const signOutListeners = new Set<() => void>();

const channel: BroadcastChannel | null =
  typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(SESSION_CHANNEL);
// 테스트 환경(Node)에서 채널이 프로세스 종료를 막지 않게 한다. 브라우저에는 이 메서드가 없다.
(channel as unknown as { unref?: () => void } | null)?.unref?.();
channel?.addEventListener("message", (e: MessageEvent) => {
  const msg = readSessionMessage(e.data);
  if (msg?.type === "access") adopt(msg.access);
  if (msg?.type === "signed-out") {
    // 서버 쪽 폐기는 로그아웃한 탭이 이미 요청했으므로 이 탭은 메모리만 비운다.
    clearSession("signed-out");
    for (const listener of [...signOutListeners]) listener();
  }
});

/** 이 탭이 받은 새 access 토큰을 다른 탭에 보낸다. 같은 출처의 탭만 받는다. */
function announce(access: string): void {
  try {
    channel?.postMessage({ type: "access", access });
  } catch {
    /* 채널이 닫혔으면 다른 탭은 자기 차례에 직접 갱신한다. */
  }
}

/**
 * 이 탭의 로그아웃을 다른 탭에 알린다. 사용자가 로그아웃할 때 `signOut`이 부른다.
 *
 * 알리지 않으면 다른 탭은 다음 요청에서 401을 받을 때까지 로그아웃한 계정의 화면과 캐시를 그대로 보이고,
 * 그 401을 세션 만료로 보고 비밀번호를 다시 받는 창을 띄운다. 받은 탭은 로그아웃 요청을 다시 보내지 않는다.
 * BroadcastChannel이 없는 브라우저에서는 알리지 못하고, 다른 탭은 다음 요청에서 만료 창을 띄운다.
 */
export function announceSignOut(): void {
  try {
    channel?.postMessage({ type: "signed-out" } satisfies SessionMessage);
  } catch {
    /* 채널이 닫혔으면 다른 탭은 다음 요청의 401에서 세션이 끝난 것을 안다. */
  }
}

/**
 * 다른 탭에서 로그아웃하면 부를 함수를 등록하고, 등록을 푸는 함수를 돌려준다.
 * 부를 때는 이 탭의 access 토큰을 이미 비운 뒤다.
 */
export function onSignedOutElsewhere(listener: () => void): () => void {
  signOutListeners.add(listener);
  return () => {
    signOutListeners.delete(listener);
  };
}

/** 메모리의 access token을 돌려준다. 없으면 null. */
export function getAccessToken(): string | null {
  return accessToken;
}

/** 로그인 응답의 access token을 메모리에 둔다. refresh 토큰은 응답의 쿠키로 이미 저장됐다. */
export function setSession(access: string): void {
  dropLegacyTokens();
  adopt(access);
}

/**
 * 메모리의 토큰과 저장소의 옛 토큰을 지운다. 서버에는 알리지 않는다(서버 쪽 폐기는 `revokeSession`).
 *
 * `reason`은 세션이 끝난 이유다. 기본값 `expired`는 401로 세션이 끊긴 경우이고, 보호 셸은 돌아올 주소를 붙여
 * 로그인 화면으로 보낸다. 사용자가 로그아웃한 경우에는 `signed-out`을 주고, 보호 셸은 `/login`만 쓴다.
 */
export function clearSession(reason: "expired" | "signed-out" = "expired"): void {
  accessToken = null;
  endedBySignOut = reason === "signed-out";
  dropLegacyTokens();
}

/** 이 탭의 세션이 사용자의 로그아웃으로 끝났으면 true. 보호 셸이 로그인 주소에 돌아올 주소를 붙일지 정할 때 읽는다. */
export function sessionEndedBySignOut(): boolean {
  return endedBySignOut;
}

/* 활동(activity) 추적. 활동 중이면 세션을 백그라운드에서 갱신하고, 활동이 없으면 그대로 만료시키는 최소 상태다.
 * 강한 신호는 `api()`가 기록하는 GET이 아닌 요청(저장·제출·가져오기)이다. 폴링(GET)을 세면 아무도 보지 않는 탭의 세션이 끝나지 않는다.
 * 약한 신호는 셸의 keydown·click·scroll 리스너가 기록하고, 커서 흔들림을 활동으로 오인하지 않도록 마우스 이동은 뺀다.
 * 마지막 활동 후 IDLE_LIMIT_MS가 지나면 갱신을 멈추고, 남은 토큰 TTL이 지나면 세션이 만료된다. */
export const IDLE_LIMIT_MS = 30 * 60 * 1000; // 30분 동안 활동이 없으면 자동 갱신을 멈춘다. 그 뒤에는 다시 로그인해야 한다.

let lastActivityAt = Date.now();

/** 사용자 활동 시각을 지금으로 기록한다. `api()`(GET이 아닌 요청)와 셸 입력 리스너가 부른다. */
export function markActivity(): void {
  lastActivityAt = Date.now();
}

/** 마지막 활동 이후 지난 시간(ms)을 돌려준다. */
export function msSinceActivity(): number {
  return Date.now() - lastActivityAt;
}

/** 마지막 활동 후 기준 시간(기본 IDLE_LIMIT_MS)이 지났으면 true. 참이면 자동 갱신을 하지 않는다. */
export function isIdle(limitMs: number = IDLE_LIMIT_MS): boolean {
  return msSinceActivity() > limitMs;
}

/**
 * 서버에 로그아웃을 요청한다. 서버는 refresh 쿠키와 헤더의 access token을 폐기(revoke)하고 쿠키를 지운 뒤
 * `auth.logout` 감사 기록을 남긴다. access token을 헤더로 보내지 않으면 그 토큰은 로그아웃한 뒤에도 만료까지 통한다.
 *
 * 메모리만 비우면 화면에서는 로그아웃되지만 refresh 쿠키는 서버에서 계속 유효해서 새로고침하면 세션이 돌아온다.
 * `내 계정`의 로그인 이력은 `auth.*` 이벤트를 보여 주므로 이 요청이 있어야 로그아웃이 이력에 남는다.
 *
 * 서버 호출이 실패해도 로그아웃은 끝나야 한다.
 * 그래서 호출하는 쪽(`signOut`)은 결과를 기다리지 않고, 실패해도 로컬 세션을 반드시 비운다.
 * 토큰은 첫 `await` 전에 읽으므로 바로 뒤에 `clearSession`을 불러도 된다.
 */
export async function revokeSession(): Promise<void> {
  const access = getAccessToken();
  const legacy = readStorage(LEGACY_REFRESH_KEY);
  try {
    await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...(access ? { Authorization: `Bearer ${access}` } : {}),
      },
      body: JSON.stringify(legacy ? { refresh_token: legacy } : {}),
      keepalive: true, // 탭이 닫히는 중에도 요청이 나가게 한다.
    });
  } catch {
    // 실패는 무시한다. 호출하는 쪽(signOut)의 clearSession()은 결과와 관계없이 실행된다.
  }
}

/**
 * refresh 한 번의 결과.
 *
 * `rejected`는 서버가 401로 refresh를 거절한 경우다(쿠키 없음·무효·폐기·세션 절대 수명 경과). 세션을 이을 수 없다.
 * `unavailable`은 5xx·429·연결 실패처럼 서버 상태 때문에 갱신하지 못한 경우다. 쿠키는 아직 유효할 수 있으므로
 * 세션을 지우지 않는다. `error`는 화면이 연결 문제나 서버 오류를 안내할 때 쓰는 실패다.
 */
export type RefreshOutcome =
  | { kind: "ok" }
  | { kind: "rejected" }
  | { kind: "unavailable"; error: ApiError };

/* refresh 요청은 탭 안에서 한 번에 하나만 보낸다. 401이 한꺼번에 와도 재발급은 한 번만 하고, 기다린 요청은 같은 결과를 받는다.
 * 진행 중인 Promise를 공유하고, 끝나면 비워 다음 만료 때 다시 발급한다. */
let inflightRefresh: Promise<RefreshOutcome> | null = null;

/**
 * 쿠키로 access 토큰을 다시 받는다(rotation). 동시 호출은 탭 안에서 한 번으로, 탭 사이에서는 Web Locks로 한 번에 하나로 합친다.
 * 성공하면 새 access 토큰을 메모리에 두고 다른 탭에 보낸 뒤 `ok`를 돌려준다.
 * 서버가 401이면 `rejected`, 그 밖의 실패(5xx·429·연결 실패)는 `unavailable`이다. 어느 쪽이든 메모리의 토큰은 지우지 않는다.
 * 지우는 일은 부르는 쪽이 결과를 보고 한다.
 *
 * 탭들은 refresh 쿠키 하나를 같이 쓰고 서버는 교체 때마다 쿠키를 바꾼다. 두 탭이 같은 쿠키로 동시에 갱신하면
 * 진 쪽이 401을 받아 로그아웃되므로, 다른 탭이 갱신 중이면 끝나기를 기다렸다가 그 탭이 보낸 새 토큰을 받는다.
 * 토큰이 `SHARE_WAIT_MS` 안에 오지 않으면 바뀐 쿠키로 직접 갱신한다.
 * Web Locks가 없는 브라우저에서는 탭 사이 조율 없이 보내고, 진 쪽의 401은 서버의 재사용 유예(30초) 안이라
 * family를 끊지 않는다. 401을 받는 동안 다른 탭의 토큰이 도착했으면 `ok`로 본다.
 */
export function refreshSession(): Promise<RefreshOutcome> {
  if (!inflightRefresh) {
    const startGen = generation;
    inflightRefresh = coordinated(startGen).finally(() => {
      inflightRefresh = null;
    });
  }
  return inflightRefresh;
}

/** Web Locks의 최소 형태. 브라우저와 테스트의 가짜 구현이 같은 꼴을 따른다. */
interface LockManagerLike {
  request<T>(name: string, cb: (lock: unknown) => Promise<T>): Promise<T>;
  request<T>(
    name: string,
    opts: { ifAvailable?: boolean },
    cb: (lock: unknown) => Promise<T>,
  ): Promise<T>;
}

/** 탭 사이 refresh를 조율한다. 다른 탭이 갱신 중이면 그 결과를 받고, 아니면 직접 갱신한다. */
async function coordinated(startGen: number): Promise<RefreshOutcome> {
  const locks = (globalThis.navigator as { locks?: LockManagerLike } | undefined)?.locks;
  if (!locks) return refreshOnce(startGen);
  const first = await locks.request(REFRESH_LOCK, { ifAvailable: true }, async (lock) =>
    lock ? refreshOnce(startGen) : null,
  );
  if (first) return first;
  return locks.request(REFRESH_LOCK, async () => {
    if (await sharedToken(startGen)) return { kind: "ok" } as const;
    return refreshOnce(startGen);
  });
}

/** `startGen` 뒤로 다른 탭이나 로그인이 access 토큰을 바꿨으면 참. 아직이면 `SHARE_WAIT_MS`까지 기다린다. */
function sharedToken(startGen: number): Promise<boolean> {
  if (generation !== startGen) return Promise.resolve(true);
  return new Promise((resolve) => {
    const wake = () => {
      clearTimeout(timer);
      resolve(true);
    };
    const timer = setTimeout(() => {
      generationWaiters.delete(wake);
      resolve(false);
    }, SHARE_WAIT_MS);
    generationWaiters.add(wake);
  });
}

/** `/auth/refresh`를 한 번 부른다. 저장소에 옛 refresh 토큰이 있으면 본문에 실어 쿠키로 바꾼다. */
async function refreshOnce(startGen: number): Promise<RefreshOutcome> {
  const legacy = readStorage(LEGACY_REFRESH_KEY);
  let r: Response;
  try {
    r = await fetch("/api/auth/refresh", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(legacy ? { refresh_token: legacy } : {}),
    });
  } catch {
    return { kind: "unavailable", error: networkError() };
  }
  if (r.ok) {
    try {
      const s = (await r.json()) as { access_token: string };
      dropLegacyTokens();
      adopt(s.access_token);
      announce(s.access_token);
      return { kind: "ok" };
    } catch {
      // 본문이 끊긴 경우다. 서버는 교체를 마쳤을 수 있지만 새 토큰을 받지 못했으므로 연결 실패로 본다.
      return { kind: "unavailable", error: networkError() };
    }
  }
  if (generation !== startGen && accessToken) return { kind: "ok" };
  if (r.status === 401) {
    // 거절된 옛 토큰은 다시 보내도 같은 결과라 지운다.
    dropLegacyTokens();
    return { kind: "rejected" };
  }
  return { kind: "unavailable", error: await responseError(r) };
}

/** 세션을 연장한다(수동 '연장' 버튼). refreshSession의 별칭이다. 성공하면 만료 시각이 늘어난다. */
export function extendSession(): Promise<RefreshOutcome> {
  return refreshSession();
}

/* 앱 시작 때 세션 복구가 일시 오류로 끝났을 때의 실패. 셸이 로그인 화면 대신 연결 오류를 안내할 때 읽는다. */
let restoreFailure: ApiError | null = null;

/**
 * 앱 시작 때 refresh 쿠키로 access 토큰을 받는다. 렌더 전에 한 번 부른다(`main.tsx`).
 *
 * 저장소의 옛 access 토큰은 쓰지 않고 지운다. 옛 refresh 토큰이 있으면 이번 요청의 본문으로 보내 쿠키로 바꾼다.
 * 일시 오류면 `RESTORE_RETRY_MS`만큼 기다리며 다시 시도한다. 끝내 일시 오류면 쿠키를 두고
 * `restoreUnavailable()`을 참으로 둔다. 셸은 그때 로그인 화면으로 보내지 않고 연결 오류를 안내한다.
 * 요청 한도(429)는 곧바로 다시 시도하지 않는다. 서버 한도는 1분 창이라 몇 초 안의 재시도는 같은 429를 받고
 * 한도만 더 쓴다. 셸이 `restoreRetryAfter()`의 초만큼 기다린 뒤 다시 연다.
 */
export async function restoreSession(): Promise<RefreshOutcome> {
  try {
    if (typeof localStorage !== "undefined") localStorage.removeItem(LEGACY_ACCESS_KEY);
  } catch {
    /* 저장소가 막혔으면 지울 것도 없다. */
  }
  if (accessToken) return { kind: "ok" };
  let out = await refreshSession();
  for (const wait of RESTORE_RETRY_MS) {
    if (out.kind !== "unavailable" || out.error.status === 429) break;
    await new Promise((r) => setTimeout(r, wait));
    out = await refreshSession();
  }
  restoreFailure = out.kind === "unavailable" ? out.error : null;
  return out;
}

/** 앱 시작 때 세션 복구가 서버 일시 오류로 끝났으면 true. 이때 쿠키의 세션은 살아 있을 수 있다. */
export function restoreUnavailable(): boolean {
  return restoreFailure !== null && !accessToken;
}

/**
 * 앱 시작 때 세션 복구가 요청 한도(429)로 끝났으면 다시 시도할 때까지 기다릴 초를 돌려준다. 아니면 null이다.
 * 서버가 `Retry-After`를 주지 않았으면 한도의 창 길이(`RATE_LIMIT_WAIT_S`)를 쓴다.
 */
export function restoreRetryAfter(): number | null {
  if (!restoreUnavailable() || restoreFailure?.status !== 429) return null;
  return restoreFailure.retryAfter ?? RATE_LIMIT_WAIT_S;
}

/**
 * access 토큰의 만료 시각이 지났을 때 세션이 끝났는지 판정한다. 셸이 만료 안내를 띄울지 정할 때 부른다.
 *
 * 만료 시각이 남았으면 거짓이다. 지났고 유휴 상태면 참이다.
 * 활동 중이면 refresh를 한 번 시도하고, 서버가 401로 거절했을 때만 참이다.
 * refresh가 일시 오류면 거짓이라 세션을 그대로 두고 다음 확인 때 다시 갱신한다.
 */
export async function accessLapsed(): Promise<boolean> {
  const exp = getSessionExpiryMs();
  if (exp == null || exp > Date.now()) return false;
  if (isIdle()) return true;
  return (await refreshSession()).kind === "rejected";
}

/** 메모리에 access token이 있으면 true를 돌려준다. 토큰이 유효한지는 확인하지 않는다. */
export function isAuthed(): boolean {
  return !!accessToken;
}

/**
 * access token JWT의 exp(초)를 epoch ms로 바꾼다.
 * 토큰이 없거나 파싱에 실패하면 null을 돌려준다(검증은 BE가 한다).
 */
export function getSessionExpiryMs(): number | null {
  const tok = getAccessToken();
  if (!tok) return null;
  try {
    const payload = JSON.parse(atob(tok.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload.exp === "number" ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}
