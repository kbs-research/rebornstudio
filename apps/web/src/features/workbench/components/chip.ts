/**
 * 워크벤치 칩의 크기 기준. 라벨링·검수 두 워크벤치가 같은 기준을 쓴다.
 *
 * `ui/Chip`은 일반 화면의 기준 칩(`text-caption`, 12px)이고 워크벤치 칩은 한 단계 작다.
 * 호출부마다 크기를 직접 적으면 같은 뜻의 표식이 파일마다 다른 크기가 되므로 여기에 모은다.
 *
 * - `WB_CHIP`: 단독 배지. 자기 필드나 줄을 차지하고 먼저 읽힌다.
 * - `WB_CHIP_TIGHT`: 다른 글 옆에 붙는 표식. 뒤따르는 문장을 한정하므로 문장보다 작다(문장의 `지적`·`교정됨`, 지적의 출처).
 *
 * 누르는 캡슐 버튼(반려 사유 토글)과 문장 번호 원·상태 점 같은 도형은 칩이 아니라서 이 기준을 쓰지 않는다.
 * 절 제목 옆 배지(`label_parts`의 `SECTION_BADGE`)는 제목 글줄 안에 `align-middle`로 얹히므로 `py-px`를 유지한다.
 * `py-0.5`로 올리면 절 제목의 줄 높이가 함께 늘어난다.
 *
 * 색은 호출부가 `bg-*-muted text-*-text`로 붙인다. 색조는 쓰는 곳의 뜻을 나타내므로 크기와 함께 고정하지 않는다.
 */

/** 단독 배지의 클래스. 워크벤치의 기본 크기(11px)이고, 가장 많이 쓰는 조합이다. */
export const WB_CHIP =
  "inline-flex items-center gap-1 rounded-capsule px-2 py-0.5 text-micro font-medium";

/** 옆에 붙는 표식의 클래스. 한 단계 작다(10px). 뒤따르는 문장이 본문이고 표식은 한정어다. */
export const WB_CHIP_TIGHT =
  "inline-flex items-center gap-0.5 rounded-capsule px-1.5 py-0.5 text-nano font-medium";

/**
 * 반려 사유 칩의 클래스. 라벨링 재작업 카드의 반려 사유에 쓴다.
 *
 * 사유는 태그라서 판정 색을 입히지 않는다. 반려라는 사실은 카드 제목(`반려 N회`)에 이미 있다.
 * 같은 사유가 화면마다 다른 색·굵기로 보이면 다른 뜻으로 읽히므로 클래스를 한 곳에 둔다.
 */
export const REASON_CHIP =
  "inline-flex items-center rounded-mark border border-separator-strong px-1.5 py-px text-micro font-semibold text-label";
