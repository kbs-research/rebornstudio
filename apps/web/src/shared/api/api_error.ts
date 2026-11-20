/**
 * 실패 응답을 `ApiError`로 바꾸는 규칙. `client.ts`의 요청과 `auth.ts`의 refresh가 같이 쓴다.
 *
 * refresh 실패도 화면에 같은 꼴로 올라가야 `apiErrorText`가 연결 문제·서버 오류·요청 한도를 같은 문구로 안내한다.
 * 두 파일이 따로 읽으면 대기 시간이 한쪽에서만 빠진다.
 */
import { validationItemText } from "./error_text";

/**
 * 422 검증 실패의 필드 하나. 폼이 필드 옆에 그린다.
 * `path`는 `loc`에서 위치(body·query…)를 뗀 점 경로다.
 */
export type FieldError = { path: string; message: string };

/**
 * `api()`가 던지는 실패. `detail`은 항상 문자열이다.
 *
 * FastAPI 검증 실패(422)의 `detail`은 `[{loc, msg, type}]` 배열로 오므로 `responseError`에서 나눈다.
 * 배열이면 `detail = "validation_error"`(가리킬 필드가 없으면 `"validation_error_no_field"`)로 두고
 * 필드별 사유는 `fields`에 담는다.
 * 배열을 그대로 실어 던지면 `errorText`가 `detail.startsWith`에서 `TypeError`를 낸다.
 * TanStack이 onError 안의 예외를 삼키므로 토스트가 뜨지 않고 저장 실패가 화면에 표시되지 않는다.
 */
export type ApiError = {
  /** HTTP 상태 코드. 응답을 받지 못했으면 0이다(`detail`은 `network_error`). */
  status: number;
  detail: string;
  /** 필드별 검증 실패 사유. 422 검증 실패일 때만 있고 사유는 한국어로 옮긴 문장이다. */
  fields?: FieldError[];
  /**
   * 서버가 `Retry-After` 헤더로 준 대기 시간(초, 429 등).
   * 화면이 "N분 뒤"를 표시할 때 쓴다.
   */
  retryAfter?: number;
  /**
   * 409 사용 중 거절(`connection_in_use`, `allowed_bucket_in_use`)에서 요청을 막은 프로젝트 이름.
   * 서버는 이름순으로 앞의 일부만 싣고 나머지는 `projectsMore`에 수로 싣는다.
   */
  projects?: string[];
  /** `projects`에 싣지 않은 나머지 프로젝트 수. */
  projectsMore?: number;
};

/** 사용 중 거절을 막은 프로젝트. `names`는 서버가 실은 이름, `more`는 싣지 않은 나머지 수다. */
export type InUseProjects = { names: string[]; more: number };

/**
 * 실패 객체에서 요청을 막은 프로젝트를 꺼낸다. 이름이 하나도 없으면 null을 돌려준다.
 * 화면은 null이면 오류 사전의 문구만 보이고, 값이 있으면 먼저 바꿀 프로젝트를 함께 보인다.
 */
export function inUseProjects(e: unknown): InUseProjects | null {
  const err = (typeof e === "object" && e !== null ? e : {}) as { projects?: unknown; projectsMore?: unknown };
  if (!Array.isArray(err.projects)) return null;
  const names = err.projects.filter((n): n is string => typeof n === "string" && n !== "");
  if (names.length === 0) return null;
  const more = typeof err.projectsMore === "number" && err.projectsMore > 0 ? Math.floor(err.projectsMore) : 0;
  return { names, more };
}

/**
 * 응답을 받지 못한 경우(연결 끊김·서버 중단)의 실패를 만든다.
 * `TypeError("Failed to fetch")`를 그대로 넘기면 `e.message`를 띄우는 화면에 영어 문장이 나가고,
 * 상태 코드가 없어 원인을 가를 수 없다.
 */
export function networkError(): ApiError {
  return { status: 0, detail: "network_error" };
}

/** Pydantic `loc`에서 앞의 위치(body·query·path·header·cookie)를 떼고 점으로 잇는다. */
function locPath(loc: unknown): string {
  if (!Array.isArray(loc)) return "";
  const parts = loc.map(String);
  if (["body", "query", "path", "header", "cookie"].includes(parts[0] ?? "")) parts.shift();
  return parts.join(".");
}

/** 응답 본문의 `detail`을 `ApiError`의 두 필드(문자열 detail·fields)로 나눈다. */
function splitDetail(raw: unknown, statusText: string): Pick<ApiError, "detail" | "fields"> {
  if (typeof raw === "string") return { detail: raw };
  if (Array.isArray(raw)) {
    const fields = raw.map((it) => ({
      path: locPath((it as { loc?: unknown } | null)?.loc),
      message: validationItemText(it),
    }));
    /*
      고칠 입력란이 있는 422와 없는 422를 나눈다.
      폼이 보낸 값이 틀렸으면 필드 경로가 오고, 화면은 그 입력란 옆에 사유를 그린다.
      본문 자체가 빠졌거나 경로·쿼리가 틀린 422는 경로가 `body` 하나뿐이라 가리킬 입력란이 없다.
      그래서 이때 "입력값이 올바르지 않습니다"라고 안내하면 틀린 안내가 된다.
    */
    const pointable = fields.some((f) => f.path && f.path !== "body");
    return { detail: pointable ? "validation_error" : "validation_error_no_field", fields };
  }
  // 서버는 문자열이나 배열만 보낸다. 다른 모양이 오면 원문 대신 상태 문구를 둔다.
  // errorText가 상태 문구를 일반 문구로 바꾼다.
  return { detail: statusText };
}

/** `Retry-After` 헤더(초 또는 HTTP 날짜)를 초로 바꾼다. 없거나 읽지 못하면 undefined. */
function parseRetryAfter(v: string | null): number | undefined {
  if (!v) return undefined;
  const secs = Number(v);
  if (Number.isFinite(secs) && secs >= 0) return Math.round(secs);
  const at = Date.parse(v);
  if (Number.isNaN(at)) return undefined;
  return Math.max(0, Math.round((at - Date.now()) / 1000));
}

/**
 * 실패 응답(2xx가 아닌 응답)을 `ApiError`로 바꾼다. 본문을 읽으므로 응답 하나에 한 번만 부른다.
 * 본문이 JSON이 아니면(프록시 502 HTML 등) 상태 문구를 `detail`로 쓴다.
 * 본문에 막는 프로젝트 이름(`projects`, `projects_more`)이 있으면 `projects`, `projectsMore`로 옮긴다.
 */
export async function responseError(r: Response): Promise<ApiError> {
  type ErrorBody = { detail?: unknown; projects?: unknown; projects_more?: unknown };
  let body: ErrorBody | null = null;
  try {
    const parsed: unknown = await r.json();
    if (typeof parsed === "object" && parsed !== null) body = parsed as ErrorBody;
  } catch {
    /* 본문이 JSON이 아니면 아래에서 상태 문구를 쓴다. */
  }
  const err: ApiError = { status: r.status, ...splitDetail(body?.detail ?? r.statusText, r.statusText) };
  const retryAfter = parseRetryAfter(r.headers.get("Retry-After"));
  if (retryAfter !== undefined) err.retryAfter = retryAfter;
  const blocking = inUseProjects({ projects: body?.projects, projectsMore: body?.projects_more });
  if (blocking) {
    err.projects = blocking.names;
    err.projectsMore = blocking.more;
  }
  return err;
}
