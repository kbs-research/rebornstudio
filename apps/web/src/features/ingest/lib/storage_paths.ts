/**
 * 버킷 경로 직접 입력의 정리 규칙. 등록 창의 저장소 탭(`StoragePathInput`)이 쓴다.
 *
 * 경로는 사람이 입력하거나 붙여 넣는 값이라 앞뒤 공백, 맨 앞 `/`, 같은 줄의 반복이 섞인다.
 * 서버도 앱 경로·영상 확장자·저장소 존재를 다시 확인하므로 여기서는 보내기 전에 모양만 맞춘다.
 */
import { isVideoName } from "./videoFiles";

/** 키 입력란을 정리한 결과. */
export interface ParsedKeys {
  /** 등록할 영상 키. 입력 순서를 지키고 반복은 한 번만 남긴다. */
  keys: string[];
  /** 영상 확장자가 아니라 보내지 않는 줄. */
  skipped: string[];
}

/**
 * 한 줄에 하나씩 입력한 오브젝트 키를 정리한다.
 *
 * 빈 줄은 버리고 맨 앞 `/`는 뗀다. 버킷 키는 `/`로 시작하지 않으므로 붙여 둔 채 보내면 저장소에 없는 키가 된다.
 * 영상 확장자가 아닌 줄은 서버가 등록하지 않으므로 보내지 않고 따로 돌려준다. 화면이 그 수를 밝힌다.
 *
 * @param text 입력란의 값
 * @param exts 서버가 준 영상 확장자. 없으면 예비 목록을 쓴다.
 */
export function parseKeyLines(text: string, exts?: ReadonlySet<string>): ParsedKeys {
  const keys: string[] = [];
  const skipped: string[] = [];
  const seen = new Set<string>();
  for (const line of text.split(/\r?\n/)) {
    const key = line.trim().replace(/^\/+/, "");
    if (!key || seen.has(key)) continue;
    seen.add(key);
    (isVideoName(key, exts) ? keys : skipped).push(key);
  }
  return { keys, skipped };
}

/**
 * 입력한 폴더 경로를 서버에 보낼 prefix로 바꾼다. 비었으면 빈 문자열이다.
 *
 * 끝에 `/`를 붙인다. 저장소 목록은 앞부분 일치라서 `news/2026`으로 보내면 `news/2026-old/` 아래 파일까지 등록된다.
 * 버킷 전체(빈 값이나 `/`)는 서버가 앱 경로와 겹친다는 이유로 거절하므로 화면에서도 보내지 않는다.
 */
export function folderPrefix(raw: string): string {
  const p = raw.trim().replace(/^\/+/, "");
  if (!p) return "";
  return p.endsWith("/") ? p : `${p}/`;
}

/** 서버가 저장소에 없는 키를 알린 오류 코드의 머리. */
const KEY_NOT_FOUND = "storage_key_not_found:";

/**
 * 등록 실패 코드에서 저장소에 없는 키를 꺼낸다. 그 오류가 아니면 `null`이다.
 *
 * 서버는 하나라도 없는 키가 있으면 아무것도 등록하지 않고 첫 번째 없는 키를 코드 뒤에 붙인다.
 */
export function missingKeyOf(detail: unknown): string | null {
  if (typeof detail !== "string" || !detail.startsWith(KEY_NOT_FOUND)) return null;
  return detail.slice(KEY_NOT_FOUND.length) || null;
}
