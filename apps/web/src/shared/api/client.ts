/**
 * 타입이 붙은 fetch 래퍼. @reborn/contracts의 paths/components 타입을 쓴다.
 *
 * 사용 예:
 *   const me = await api<UserDTO>("/auth/me");
 *   const login = await api<LoginResponse>("/auth/login", { method: "POST", body });
 */
import type { components } from "@reborn/contracts";

import { type ApiError, networkError, responseError } from "./api_error";
import { clearSession, getAccessToken, isIdle, markActivity, refreshSession } from "./auth";

export type { ApiError, FieldError, InUseProjects } from "./api_error";
export { inUseProjects } from "./api_error";

const BASE = "/api"; // vite.config.ts의 프록시 경로.

/**
 * 이 실패가 권한 오류(403)이면 true. 권한 오류면 화면에서 빼고, 아니면 실패를 표시한다.
 *
 * `error`에는 403 외에 500·타임아웃·네트워크 끊김도 같은 경로로 온다.
 * 그때 카드가 표시 없이 사라지면 페이지는 정상처럼 보이고, 관리자는 그 카드가 있었다는 것도 알 수 없다.
 * 그래서 `if (q.error) return null`처럼 모든 실패에서 카드를 없애는 대신 이 함수로 권한 오류만 가린다.
 *
 * 401은 `api()`가 세션 만료로 따로 처리해 모달을 띄우므로 이 함수가 다루지 않는다.
 */
export function isForbidden(e: unknown): boolean {
  return typeof e === "object" && e !== null && (e as ApiError).status === 403;
}

/** `api()` 호출 옵션. 401이 세션 만료를 뜻하지 않는 엔드포인트에서는 `expect401`을 켠다. */
interface ApiOptions extends RequestInit {
  /*
    `api()`는 401을 토큰 만료로 보고 세션을 비운 뒤 만료 모달을 띄운다.
    비밀번호를 검증하는 엔드포인트의 401은 비밀번호가 틀렸다는 뜻이라, 만료로 보면 한 글자만 틀려도 로그인 화면으로 이동한다.
    그런 엔드포인트에서만 이 값을 켠다. 기본값은 401을 세션 만료로 본다.
  */
  expect401?: boolean;
}

/**
 * API를 호출하고 JSON 응답을 돌려준다. 실패하면 `ApiError`를 던진다.
 * 401이면 활동 중일 때 세션 갱신을 한 번 시도한 뒤 재요청한다.
 * 본문이 `FormData`이면 파일 업로드로 보낸다(`Content-Type`은 브라우저가 경계 문자열과 함께 붙인다).
 */
export async function api<T>(path: string, init: ApiOptions = {}): Promise<T> {
  const r = await send(path, init);
  // 204 No Content
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

/** `apiFile`이 돌려주는 내려받은 파일. */
export interface ApiFile {
  blob: Blob;
  /** 서버가 `Content-Disposition`에 적은 파일 이름. 없으면 `null`이다. */
  filename: string | null;
}

/**
 * 파일 응답을 받는 API를 호출한다. 인증·세션 갱신·실패 처리는 `api()`와 같다.
 *
 * 서명된 저장소 주소가 아니라 API가 직접 만드는 파일(양식 등)에 쓴다. 주소를 링크로 열면
 * Authorization 헤더를 실을 수 없어서 blob으로 받는다.
 */
export async function apiFile(path: string, init: ApiOptions = {}): Promise<ApiFile> {
  const r = await send(path, init);
  return { blob: await r.blob(), filename: dispositionName(r.headers.get("Content-Disposition")) };
}

/** `Content-Disposition` 헤더에서 파일 이름을 꺼낸다. `filename*=UTF-8''…`을 먼저 읽는다. */
export function dispositionName(v: string | null): string | null {
  if (!v) return null;
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(v);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      /* 잘못 인코딩된 값이면 일반 filename을 읽는다. */
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(v);
  return plain ? plain[1].trim() : null;
}

/**
 * 요청을 보내고 성공 응답을 돌려준다. `api()`와 `apiFile()`이 함께 쓴다.
 * 401이면 활동 중일 때 세션 갱신을 한 번 시도한 뒤 재요청하고, 실패 응답은 `ApiError`로 던진다.
 * 갱신이 401이면 세션을 비우고 만료를 알린다. 갱신이 일시 오류면 세션을 두고 그 실패를 던진다.
 */
async function send(
  path: string,
  init: ApiOptions,
  // 401 뒤 refresh로 토큰을 다시 받아 한 번 재시도할 때만 true다.
  retried = false,
): Promise<Response> {
  const { expect401, ...rest } = init;
  const headers = new Headers(rest.headers);
  if (!(rest.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const tok = getAccessToken();
  if (tok) headers.set("Authorization", `Bearer ${tok}`);

  // 사용자가 시작한 변경 요청(GET이 아닌 요청)은 실제 작업 신호라서 세션 자동 갱신의 1차 기준이다.
  // 백그라운드 폴링(GET)을 활동으로 세면 아무도 보지 않는 탭의 세션이 계속 유지되므로 세지 않는다.
  const method = (rest.method ?? "GET").toUpperCase();
  if (method !== "GET") markActivity();

  let r: Response;
  try {
    r = await fetch(`${BASE}${path}`, { ...rest, headers });
  } catch {
    throw networkError();
  }
  if (r.status === 401 && !expect401) {
    // 만료 직전·직후의 401을 받으면 활동 중일 때 refresh로 한 번 갱신하고 재시도한다.
    // 유휴 상태면 갱신하지 않는다. 그래야 자리를 비운 세션이 idle 만료로 끝난다.
    // 이 탭에 세션이 없었으면(토큰 없이 보낸 요청) 갱신할 세션도 없다.
    if (!retried && !isIdle() && tok) {
      const out = await refreshSession();
      if (out.kind === "ok") return send(path, init, true);
      // refresh가 일시 오류(5xx·429·연결 실패)면 세션을 두고 이 요청만 그 실패로 돌린다.
      // 토큰은 그대로라 다음 요청이 401을 받으면 다시 갱신한다. 기다린 요청마다 실패 객체를 따로 준다.
      if (out.kind === "unavailable") throw { ...out.error };
    }
    clearSession();
    // 세션 만료를 셸(_layout)에 알린다. 셸은 정보를 불러오지 못하는 이유를 모달로 안내한다.
    window.dispatchEvent(new CustomEvent("reborn:session-expired"));
    throw { status: 401, detail: "unauthorized" } satisfies ApiError;
  }
  if (!r.ok) throw await responseError(r);
  return r;
}


// 도메인 단축 별칭.
export type UserDTO = components["schemas"]["UserDTO"];
export type ProjectDTO = components["schemas"]["ProjectDTO"];
export type ProjectStats = components["schemas"]["ProjectStats"];
export type VideoDTO = components["schemas"]["VideoDTO"];
export type ClipDTO = components["schemas"]["ClipDTO"];
export type ClipCountDTO = components["schemas"]["ClipCountDTO"];
export type ConnectionDTO = components["schemas"]["ConnectionDTO"];
export type ConnectionSaved = components["schemas"]["ConnectionSaved"];
export type ConnectionProvider = components["schemas"]["ConnectionDTO"]["provider"];
export type LabelerPerfRow = components["schemas"]["LabelerPerfRow"];
export type LabelerPerfSummary = components["schemas"]["LabelerPerfSummary"];
export type BusinessKpi = components["schemas"]["BusinessKpi"];
export type HoursProgress = components["schemas"]["HoursProgress"];
export type ProjectMemberDTO = components["schemas"]["ProjectMemberDTO"];
export type WorkflowHealth = components["schemas"]["WorkflowHealth"];
export type PipelineSummary = components["schemas"]["PipelineSummary"];
export type VideoBucketCounts = components["schemas"]["VideoBucketCounts"];
export type DomainEventDTO = components["schemas"]["DomainEventDTO"];
export type LabelDTO = components["schemas"]["LabelDTO"];
export type ReviewDTO = components["schemas"]["ReviewDTO"];
export type LoginResponse = components["schemas"]["LoginResponse"];
export type DiagnosticsReport = components["schemas"]["DiagnosticsReport"];
