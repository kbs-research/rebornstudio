/**
 * 탭 제목의 형식과 화면 이름 올리기·내리기를 검증한다.
 *
 * 탭 제목은 화면에 안 보여서 틀려도 탭을 여럿 열어 보기 전에는 드러나지 않는다.
 */
import { describe, expect, it } from "vitest";

import { APP_NAME, claimPageTitle, currentPageTitle, formatDocumentTitle } from "./useDocumentTitle";

describe("formatDocumentTitle", () => {
  it("화면 · 프로젝트 · 앱 이름 순서다", () => {
    expect(formatDocumentTitle("콘텐츠", "Default Project")).toBe("콘텐츠 · Default Project · RebornStudio Essential");
  });

  it("프로젝트 밖 화면은 가운데 조각을 뺀다", () => {
    expect(formatDocumentTitle("사용자", null)).toBe("사용자 · RebornStudio Essential");
  });

  it("이름이 없으면 앱 이름만 남는다", () => {
    expect(formatDocumentTitle(null, null)).toBe(APP_NAME);
    expect(formatDocumentTitle("  ", "")).toBe(APP_NAME);
  });
});

describe("화면 이름 올리기", () => {
  it("가장 나중에 올린 이름이 이기고, 내리면 앞의 이름으로 돌아간다", () => {
    const releaseList = claimPageTitle("콘텐츠");
    const releaseDetail = claimPageTitle("저녁 뉴스 샘플 2026.05.14");
    expect(currentPageTitle()).toBe("저녁 뉴스 샘플 2026.05.14");
    releaseDetail();
    expect(currentPageTitle()).toBe("콘텐츠");
    releaseList();
    expect(currentPageTitle()).toBeNull();
  });

  it("같은 이름을 둘이 올려도 하나를 내릴 때 다른 하나는 남는다", () => {
    const a = claimPageTitle("통계");
    const b = claimPageTitle("통계");
    a();
    expect(currentPageTitle()).toBe("통계");
    b();
    expect(currentPageTitle()).toBeNull();
  });
});
