/**
 * 영상 단위 메타 네 가지. 클립이 물려받는 값이다.
 *
 * 방송사·프로그램명·장르·방영일은 영상 단위의 사실이다.
 * 같은 사실을 클립 수백 개에 사람이 반복해 입력하지 않도록, 관리자가 영상 상세에서 한 번 입력하면 그 영상의 모든 클립이 같은 값을 쓴다.
 * 납품 메타·품질검수·워크벤치가 모두 영상 값을 읽고, 라벨링·검수 워크벤치는 읽기만 한다.
 *
 * 키는 서버 계약(`VideoMetaDTO`)과 같다.
 * 품질검수의 메타 검사(`caption_qc/rules._VIDEO_META`)·
 * 서버 기준 목록(`services/video_meta.FIELDS`)과 같은 집합이어야 한다.
 * 서버 테스트가 아래 줄을 읽어 세 목록을 대조한다.
 * 이름을 바꾸면 그 테스트도 함께 고친다.
 */
export const VIDEO_META_FIELDS = ["broadcaster", "program_name", "genre", "aired_at"] as const;
export type VideoMetaKey = (typeof VIDEO_META_FIELDS)[number];

/** 화면 이름. 영상 상세·워크벤치·완료 상세가 같은 이름을 쓴다(용어 표준: 방영일·프로그램명). */
export const VIDEO_META_NAME: Record<VideoMetaKey, string> = {
  broadcaster: "방송사",
  program_name: "프로그램명",
  genre: "장르",
  aired_at: "방영일",
};

/** 한 줄 안내. 이 값을 누가 어디서 채우는지 알린다. 워크벤치의 읽기 전용 필드 아래에 둔다. */
export const VIDEO_META_OWNER_NOTE = "관리자가 영상 상세에서 채우며 이 영상의 모든 클립에 같은 값이 쓰입니다";

/** 방영일을 KST 달력일로 표기한다. 없거나 읽지 못하면 `null`을 돌려준다. */
export function airedDate(iso: string | null | undefined): string | null {
  const day = airedAtToInput(iso);
  if (!day) return null;
  const [y, m, d] = day.split("-").map(Number);
  return `${y}. ${m}. ${d}.`;
}

/**
 * `<input type="date">` 값(`YYYY-MM-DD`)을 저장할 시각(KST 자정)으로 바꾼다.
 * 빈 값이나 읽지 못하는 값은 `null`을 돌려준다.
 *
 * 방영일은 한국 날짜다. UTC 자정으로 보내면 KST 화면에서 하루 밀린 날짜로 읽힌다.
 * 완료 상세의 `localDay`(`CompletedClipView`)와 같은 시간 축 문제다.
 */
export function airedAtFromInput(day: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day.trim());
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const probe = new Date(Date.UTC(y, mo - 1, d));
  if (probe.getUTCMonth() !== mo - 1 || probe.getUTCDate() !== d) return null; // 2월 30일처럼 달력에 없는 날.
  return `${m[1]}-${m[2]}-${m[3]}T00:00:00+09:00`;
}

/**
 * 저장된 시각을 `<input type="date">` 값(KST 달력일)으로 바꾼다.
 * 없거나 읽지 못하면 빈 문자열을 돌려준다.
 */
export function airedAtToInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  // 방영일은 한국 날짜라서 브라우저 시간대와 관계없이 KST 달력일로 읽는다.
  return new Date(t + 9 * 3_600_000).toISOString().slice(0, 10);
}
