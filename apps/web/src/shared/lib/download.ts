/** 다운로드 헬퍼. 납품 패키지(내보내기)와 API가 만든 파일(메타 양식)을 내려받는다. */

/**
 * 서명된 파일 링크를 새 창 대신 같은 창에서 열어 내려받는다.
 *
 * 링크는 버튼을 누른 뒤 서버 왕복을 거쳐 온다.
 * 그 뒤에 `window.open`을 부르면 사용자 제스처와 분리되어 Safari 기본 설정에서 팝업으로 막히고,
 * 화면에는 아무 안내도 없다. 같은 창 이동은 팝업 차단과 관계없이 동작한다.
 * 서버가 `Content-Disposition: attachment`로 서명하므로(`storage.presign_get(filename=)`)
 * 페이지는 그대로 있고 파일만 내려받아진다.
 * 그 헤더가 빠져도 zip은 브라우저가 열 수 없어서 다운로드로 처리된다.
 *
 * 이미지 저장(`downloadImage`)과 달리 blob으로 받지 않는다.
 * 패키지는 수십 MB라 메모리에 통째로 올리지 않고, 파일 이름은 서명에 이미 정해져 있다.
 */
export function startDownload(url: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/**
 * 이미지 한 장을 저장한다.
 * presigned cross-origin URL에서는 a[download]의 파일명이 무시된다.
 * 그래서 blob으로 받아 object URL로 저장한다(파일명 보존). CORS가 실패하면 새 탭으로 연다.
 * @returns 실제 파일로 저장했으면 true, 새 탭으로 열었으면 false.
 */
export async function downloadImage(url: string, name: string): Promise<boolean> {
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(String(res.status));
    const blob = await res.blob();
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objUrl;
    a.download = name || "image";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objUrl);
    return true;
  } catch {
    window.open(url, "_blank", "noopener,noreferrer");
    return false;
  }
}

/**
 * 이미 받은 파일 내용(blob)을 정한 이름으로 저장한다.
 *
 * API가 인증 헤더를 받고 만들어 주는 파일(`apiFile`)에 쓴다. 이런 파일은 링크로 열 수 없어서
 * blob으로 받은 뒤 object URL로 저장한다.
 */
export function saveBlob(blob: Blob, name: string): void {
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objUrl;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 클릭 직후 해제하면 저장을 시작하기 전에 내용이 사라져 실패하는 브라우저가 있다. 저장이 시작될 시간을 둔다.
  setTimeout(() => URL.revokeObjectURL(objUrl), BLOB_URL_TTL_MS);
}

/** 저장용 object URL을 유지하는 시간(ms). */
export const BLOB_URL_TTL_MS = 60_000;

/**
 * 서명된 파일 링크의 내용을 받아 정한 이름으로 저장한다. 받지 못하면 같은 창 이동(`startDownload`)으로 넘어간다.
 *
 * `http` 페이지에서 다른 출처의 `https` 파일로 같은 창 이동을 하면, 내용을 다 받고도 100%에서 끝나지 않는
 * 브라우저가 있다(Chromium 계열). 내용을 페이지가 직접 받아 저장하면 저장소 응답과 브라우저의
 * 다운로드 처리가 분리되어 이 문제가 생기지 않는다. 저장소가 이 출처의 읽기를 허용하지 않거나(CORS)
 * 네트워크가 실패하면 같은 창 이동으로 연다. 이 함수는 예외를 던지지 않는다.
 *
 * Args:
 *   url: 서명된 파일 링크.
 *   name: 저장할 파일 이름. 다른 출처 응답의 `Content-Disposition`은 읽을 수 없어 부르는 쪽이 준다.
 */
export async function downloadUrl(url: string, name: string): Promise<void> {
  let blob: Blob;
  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error(String(r.status));
    blob = await r.blob();
  } catch {
    startDownload(url);
    return;
  }
  saveBlob(blob, name);
}
