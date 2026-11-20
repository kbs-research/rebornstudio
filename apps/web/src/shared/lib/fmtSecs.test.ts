/**
 * 시각 표기 테스트. 구간 양끝은 같은 단위로 쓰고, 1시간을 넘으면 시간 단위를 붙인다
 * (영상 길이와 무관하게 동작한다).
 */
import { describe, expect, it } from "vitest";

import { fmtClipRange, fmtClock, fmtH, fmtSecKo, fmtSecs, fmtTimecode } from "./fmtSecs";

describe("fmtClipRange", () => {
  it("60초 경계를 걸쳐도 양끝 단위가 같다", () => {
    expect(fmtClipRange(12, 75)).toBe("0:12–1:15");
  });
  it("1시간을 넘으면 양끝 모두 시간 자리를 연다", () => {
    expect(fmtClipRange(3500, 4898)).toBe("0:58:20–1:21:38");
  });
  it("1분 미만 구간도 타임코드다 — 한 목록 안에서 두 꼴이 섞이지 않는다", () => {
    expect(fmtClipRange(1, 55)).toBe("0:01–0:55");
    expect(fmtClipRange(29.8, 49.4)).toBe("0:29–0:49");
  });
  it("시작과 끝이 같은 초로 적힐 때만 소수 한 자리를 붙인다", () => {
    expect(fmtClipRange(2.2, 2.9)).toBe("0:02.2–0:02.9");
    expect(fmtClipRange(2.9, 3.1)).toBe("0:02–0:03");
    expect(fmtClipRange(3600.2, 3600.7)).toBe("1:00:00.2–1:00:00.7");
  });
  it("영문 단위를 쓰지 않는다", () => {
    for (const [a, b] of [[0, 0.5], [1, 55], [59, 61], [3500, 4898]] as const) {
      expect(fmtClipRange(a, b)).not.toMatch(/s$/);
    }
  });
  it("값이 없으면 빈 값", () => {
    expect(fmtClipRange(null, 5)).toBe("—");
    expect(fmtClipRange(1, Number.NaN)).toBe("—");
  });
});

describe("fmtTimecode", () => {
  it("한 지점은 길이가 아니라 위치로", () => {
    expect(fmtTimecode(12)).toBe("0:12");
    expect(fmtTimecode(3725)).toBe("1:02:05");
    expect(fmtTimecode(null)).toBe("—");
  });
});

describe("fmtClock", () => {
  it("경계는 0.1초까지", () => {
    expect(fmtClock(1220.94)).toBe("20:20.9");
    expect(fmtClock(3.4)).toBe("0:03.4");
  });
});

describe("fmtH", () => {
  it("분량은 `시간`으로 적는다 — 화면에 영문 단위를 남기지 않는다", () => {
    expect(fmtH(12.345)).toBe("12.3시간");
    expect(fmtH(0.2234)).toBe("0.22시간");
    expect(fmtH(1250)).toBe("1,250시간");
    expect(fmtH(0)).toBe("0시간");
  });
  it("자릿수는 크기에 따라 — 100 이상은 소수를 버린다", () => {
    expect(fmtH(123.456)).toBe("123시간");
    expect(fmtH(99.99)).toBe("100시간");
  });
});

describe("fmtSecs", () => {
  it("60초 이상은 분:초, 없으면 빈 값", () => {
    expect(fmtSecs(75)).toBe("1:15");
    expect(fmtSecs(null)).toBe("—");
  });
  it("60초 미만도 한국어 단위다", () => {
    expect(fmtSecs(5.25)).toBe("5.3초");
    expect(fmtSecs(0)).toBe("0.0초");
  });
});

describe("fmtSecKo", () => {
  it("초가 0이면 `0초`를 붙이지 않는다", () => {
    expect(fmtSecKo(2400)).toBe("40분");
    expect(fmtSecKo(75)).toBe("1분 15초");
  });
  it("반올림이 60초로 넘어가면 분으로 올린다", () => {
    expect(fmtSecKo(119.6)).toBe("2분");
    expect(fmtSecKo(59.6)).toBe("1분");
  });
  it("1분 미만과 큰 단위는 그대로다", () => {
    expect(fmtSecKo(12.4)).toBe("12초");
    expect(fmtSecKo(5400)).toBe("1.5시간");
    expect(fmtSecKo(null)).toBe("—");
  });
});
