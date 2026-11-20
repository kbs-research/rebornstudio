/**
 * 제품 이름과 에디션 이름. 워드마크, 사이드바 브랜드 줄, 탭 제목이 같은 값을 쓴다.
 *
 * 같은 제품에 에디션이 여럿 있어 화면과 탭 제목만 보고 어느 에디션인지 알 수 있게 에디션 이름을 함께 쓴다.
 */

/** 제품 이름. */
export const PRODUCT_NAME = "RebornStudio";

/** 에디션 이름. */
export const EDITION_NAME = "Essential";

/** 제품 이름과 에디션 이름을 합친 이름. 탭 제목의 끝 조각과 `index.html`의 기본 제목이다. */
export const PRODUCT_FULL_NAME = `${PRODUCT_NAME} ${EDITION_NAME}`;
