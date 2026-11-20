/**
 * 클립보드 복사.
 *
 * `navigator.clipboard`는 보안 컨텍스트(https·localhost)에서만 있다.
 * 평문 http 주소로 연 화면에서는 `undefined`라서 `writeText`를 부르면 TypeError가 나고
 * 호출부의 실패 알림도 뜨지 않는다.
 * 그때는 숨긴 입력란을 선택해 `document.execCommand("copy")`로 복사한다.
 * 이 명령은 폐기 예정이지만 보안 컨텍스트가 아니어도 사용자 클릭 안에서는 동작한다.
 */

/**
 * 글자를 클립보드에 복사한다. 두 경로가 모두 실패하면 거부된 Promise를 돌려준다.
 *
 * 클릭 처리 안에서 불러야 한다. `execCommand`는 사용자 동작 밖에서는 거부된다.
 *
 * @throws Error 클립보드 API와 `execCommand` 복사가 모두 실패한 경우.
 */
export async function copyText(text: string): Promise<void> {
  const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
  if (clipboard && typeof clipboard.writeText === "function") {
    try {
      await clipboard.writeText(text);
      return;
    } catch {
      // 권한 거부나 창 비활성으로 거절되면 `execCommand` 복사를 한 번 더 시도한다.
    }
  }
  if (!copyWithSelection(text)) throw new Error("clipboard_unavailable");
}

/**
 * 숨긴 입력란을 선택해 `execCommand("copy")`로 복사한다. 복사됐으면 true를 돌려준다.
 *
 * 입력란은 지금 포커스가 있는 대화상자 안에 붙인다.
 * 바깥에 붙이면 대화상자의 포커스 가두기가 포커스를 되돌려 선택이 풀리고 빈 값이 복사된다.
 */
function copyWithSelection(text: string): boolean {
  if (typeof document === "undefined" || typeof document.execCommand !== "function") return false;
  const before = document.activeElement as HTMLElement | null;
  const host = before?.closest?.<HTMLElement>("[role=dialog]") ?? document.body;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.setAttribute("aria-hidden", "true");
  // 화면 밖으로 밀면 일부 브라우저가 스크롤을 옮기므로 제자리에 두고 보이지 않게 한다.
  Object.assign(area.style, { position: "fixed", top: "0", left: "0", opacity: "0", pointerEvents: "none" });
  host.appendChild(area);
  let ok = false;
  try {
    area.select();
    area.setSelectionRange(0, text.length);
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  } finally {
    area.remove();
    before?.focus?.();
  }
  return ok;
}
