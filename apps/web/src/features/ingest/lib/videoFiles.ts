/**
 * 등록 대상 영상 파일 판정. 두 등록 경로가 이 판정을 함께 쓴다.
 *
 * 판정은 확장자로 한다. 브라우저는 `.mxf`·`.ts` 파일에 빈 MIME을 주는 경우가 많아서
 * MIME(`video/*`)으로 고르면 방송사 원본(MXF)이 알림 없이 버려지고 파일 선택창에도 보이지 않는다.
 *
 * 목록은 서버(`services/media/video._VIDEO_EXTS`)와 같아야 한다.
 * 다르면 화면이 받은 파일을 서버가 거절하거나, 서버가 받는 파일을 화면이 숨긴다.
 */

/**
 * 영상 확장자의 예비 목록. 서버 `_VIDEO_EXTS`와 같은 값이다.
 *
 * 기준은 서버 목록이다(`GET …/videos/upload-limits`). 두 목록이 다르면 서버 목록을 따른다.
 * 로컬 업로드와 버킷 경로 입력(`StoragePathInput`)은 서버 응답이 오기 전에만 이 목록으로 판정한다.
 * 그래서 시트가 열리자마자 끌어다 놓은 파일도 받는다.
 */
export const VIDEO_EXTS: ReadonlySet<string> = new Set([
  "mp4", "mov", "mkv", "avi", "ts", "m4v", "webm", "mxf",
]);

/** 파일 선택창의 `accept` 값을 만든다. 빈 MIME 파일도 선택창에 보이도록 확장자로 거른다. */
export function videoAccept(exts?: Iterable<string>): string {
  return [...(exts ?? VIDEO_EXTS)].map((e) => `.${e}`).join(",");
}

/**
 * 파일명이나 오브젝트 키의 확장자로 영상인지 판정한다. 대소문자는 구분하지 않는다.
 *
 * Args:
 *     name: 파일명 또는 오브젝트 키.
 *     exts: 서버가 준 확장자 목록. 없으면 `VIDEO_EXTS`를 쓴다.
 */
export function isVideoName(name: string, exts?: ReadonlySet<string>): boolean {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return name.includes(".") && (exts ?? VIDEO_EXTS).has(ext);
}
