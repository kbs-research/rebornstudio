/**
 * 워크벤치 단축키의 단일 출처.
 * 워크벤치 처리기와 단축키 창이 같은 표를 읽는다.
 *
 * 키를 어떻게 판정하는지(`match`)와 화면에 무엇이라 적을지(`keys`·`desc`)를 한 줄에 둔다.
 * 처리기는 `match`로 고르고 단축키 창은 같은 배열을 그대로 그린다. 그래서 한쪽만 고칠 수 없다.
 * 워크벤치마다 키를 따로 적으면 같은 키가 화면마다 다른 동작을 하고, 한쪽 키를 고칠 때 다른 쪽 도움말이 틀린 내용이 된다.
 *
 * 단축키는 재생 키, 임시저장과 제출, 문장 편집 확정과 취소만 둔다.
 * 건 이동·가이드 열기·문장 고르기처럼 버튼으로 하는 일에는 키를 두지 않는다.
 */

/** 도움말 한 줄. `ShortcutsModal`이 그대로 그린다. */
export interface ShortcutItem {
  keys: string;
  desc: string;
}

/**
 * 단축키 표의 한 줄. 화면 표기와 키 판정을 함께 가진다.
 *
 * `match`가 없는 줄은 다른 층이 처리하는 키다(`handledBy`에 처리하는 위치를 적는다).
 * 사용자는 그 키도 쓰므로 도움말에서 빠지지 않도록 표에 적어 둔다.
 */
export interface ShortcutSpec extends ShortcutItem {
  id: string;
  match?: (e: KeyboardEvent) => boolean;
  handledBy?: string;
}

/** 단축키 창의 묶음 하나. 표의 줄을 그대로 싣는다. */
export interface ShortcutGroupSpec {
  title: string;
  rows: readonly ShortcutSpec[];
}

/**
 * 단축키 판정에 쓸 키 이름을 소문자로 구한다.
 *
 * 한글 입력 상태에서는 A 위치의 키를 눌러도 `e.key`가 `ㅁ`이라 글자 키 단축키가 적용되지 않는다.
 * 입력기가 조합 중이면 `Process`가 온다. 이때는 물리 키(`e.code`의 `KeyA`)로 글자를 정한다.
 * 영문 글자가 오면 그 값을 그대로 쓴다. AZERTY 같은 다른 자판 배열에서는 물리 키보다 입력된 글자가 맞다.
 */
export function keyOf(e: Pick<KeyboardEvent, "key" | "code">): string {
  const k = e.key ?? "";
  if (/^[a-z]$/i.test(k)) return k.toLowerCase();
  const m = /^Key([A-Z])$/.exec(e.code ?? "");
  return m ? m[1].toLowerCase() : k.toLowerCase();
}

/** 수식키 없이 그 키만 눌렸는지 판정하는 함수를 만든다. 재생 키는 모두 수식키 없이 쓴다. */
function bare(...keys: string[]): (e: KeyboardEvent) => boolean {
  const set = new Set(keys.map((k) => k.toLowerCase()));
  return (e) => !e.metaKey && !e.ctrlKey && !e.altKey && set.has(keyOf(e));
}

/**
 * ⌘(맥) 또는 Ctrl(그 밖)과 함께 눌렸는지 판정하는 함수를 만든다.
 * Alt가 같이 눌린 것은 다른 조합이라 받지 않는다.
 */
function withCmd(key: string): (e: KeyboardEvent) => boolean {
  const want = key.toLowerCase();
  return (e) => (e.metaKey || e.ctrlKey) && !e.altKey && keyOf(e) === want;
}

/**
 * 재생 키. 모든 워크벤치가 함께 쓴다. 배열 순서가 도움말 순서다(자주 쓰는 키부터).
 *
 * `←`/`→`(±10초)는 `VideoPlayer`가 오버레이와 함께 window 캡처로 전담한다.
 * 이 표에서 또 처리하면 한 번 눌렀을 때 20초가 이동하므로 표기만 남기고 `match`를 주지 않는다.
 */
export const PLAYBACK_SHORTCUTS: readonly ShortcutSpec[] = [
  { id: "toggle", keys: "Space", desc: "재생 / 일시정지", match: bare(" ") },
  { id: "pause", keys: "K", desc: "일시정지", match: bare("k") },
  { id: "back10", keys: "← / →", desc: "10초 뒤로 / 앞으로", handledBy: "VideoPlayer" },
  { id: "back5", keys: "J / L", desc: "5초 뒤로 / 앞으로", match: bare("j", "l") },
  { id: "back1", keys: "[ / ]", desc: "1초 뒤로 / 앞으로", match: bare("[", "]") },
  { id: "frame", keys: ", / .", desc: "1프레임 뒤로 / 앞으로", match: bare(",", ".") },
  { id: "rate", keys: "− / =", desc: "느리게 / 빠르게 (배속)", match: bare("-", "=", "+") },
  { id: "rate1", keys: "0", desc: "배속 1배로", match: bare("0") },
];

/**
 * 라벨링 워크벤치의 저장·제출 키. `useLabelWorkbench`가 처리한다.
 *
 * 제출은 바로 보내지 않고 확인 창을 연다. 확인 창 안의 같은 키는 창이 직접 받는다(`WorkbenchConfirm`).
 */
export const LABEL_SHORTCUTS: readonly ShortcutSpec[] = [
  { id: "save", keys: "⌘/Ctrl + S", desc: "임시저장", match: withCmd("s") },
  { id: "submit", keys: "⌘/Ctrl + Enter", desc: "제출 확인 창 열기", match: withCmd("enter") },
];

/**
 * 문장 편집 상자 안의 키. `SentenceEditor`가 처리한다.
 *
 * `Enter`는 줄바꿈으로 남긴다. 문장이 여러 줄일 수 있어 그 키를 확정에 쓰면 줄바꿈 입력이 막힌다.
 */
export const EDIT_SHORTCUTS: readonly ShortcutSpec[] = [
  { id: "confirm", keys: "⌘/Ctrl + Enter", desc: "편집 확정", match: withCmd("enter") },
  { id: "cancel", keys: "Esc", desc: "편집 취소", match: (e) => e.key === "Escape" },
];

/** 라벨링 워크벤치 단축키 창의 열. 왼쪽은 재생, 오른쪽은 문장 편집과 저장·제출이다. */
export const LABEL_HELP: readonly (readonly ShortcutGroupSpec[])[] = [
  [{ title: "재생", rows: PLAYBACK_SHORTCUTS }],
  [
    { title: "문장 편집", rows: EDIT_SHORTCUTS },
    { title: "저장 · 제출", rows: LABEL_SHORTCUTS },
  ],
];

/**
 * 표에서 `id`의 줄을 찾는다.
 * `id`를 잘못 적으면 예외를 던져, 키가 오류 없이 조용히 동작하지 않는 상태를 막는다.
 */
export function spec(table: readonly ShortcutSpec[], id: string): ShortcutSpec {
  const found = table.find((s) => s.id === id);
  if (!found) throw new Error(`알 수 없는 단축키 id: ${id}`);
  return found;
}

/**
 * `id`의 키가 눌렸는지 판정한다. 처리기가 부른다.
 * `match`가 없는 줄(다른 층이 맡는 키)은 항상 false다.
 */
export function hit(table: readonly ShortcutSpec[], id: string, e: KeyboardEvent): boolean {
  return spec(table, id).match?.(e) ?? false;
}

/** 도움말이 그릴 줄 목록. 판정 함수는 빼고 표기만 넘긴다. */
export function legend(table: readonly ShortcutSpec[]): ShortcutItem[] {
  return table.map(({ keys, desc }) => ({ keys, desc }));
}

/** 단축키 창이 그릴 열 목록을 만든다. 묶음마다 표기만 넘긴다. */
export function helpColumns(
  columns: readonly (readonly ShortcutGroupSpec[])[],
): { title: string; items: ShortcutItem[] }[][] {
  return columns.map((groups) => groups.map((g) => ({ title: g.title, items: legend(g.rows) })));
}

/**
 * 단축키 표기 글자(`⌘/Ctrl + Enter`, `J / L`)를 키캡 목록으로 바꾼다.
 *
 * 워크벤치의 단축키 창과 버튼이 키 하나를 캡 하나로 그린다.
 * `⌘/Ctrl`은 `⌘` 하나로 줄인다. ⌘와 Ctrl의 관계는 단축키 창 아래 한 문장이 설명한다.
 * `Enter`는 `↵`로 그린다. 구분자는 앞뒤에 공백이 있는 `+`·`/`만 본다(`⌘/Ctrl`은 한 키다).
 */
export function keysOf(text: string): string[] {
  return text
    .split(/\s+[+/]\s+/)
    .map((k) => k.trim())
    .filter(Boolean)
    .map((k) => (k === "Enter" ? "↵" : k === "⌘/Ctrl" ? "⌘" : k));
}
