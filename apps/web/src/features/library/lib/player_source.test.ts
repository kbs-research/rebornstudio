/** 재생기 소스·구간 판정(`playerKey`·`resumeSeek`) 검사. */
import { describe, expect, it } from "vitest";

import { MEDIA_REFRESH_MAX, mediaErrorAction, playerKey, resumeSeek } from "./player_source";

describe("playerKey", () => {
  it("구간이 늦게 오면 키가 바뀌어 재생기를 새로 만든다", () => {
    expect(playerKey(undefined, undefined)).toBe("full");
    expect(playerKey(0, 79.5)).toBe("clip:0:79.5");
    expect(playerKey(undefined, undefined)).not.toBe(playerKey(0, 79.5));
  });

  it("끝이 시작보다 크지 않으면 구간이 없다", () => {
    expect(playerKey(10, 10)).toBe("full");
  });
});

describe("resumeSeek", () => {
  const playing = { abs: 2760, paused: false };

  it("주소만 바뀌면 바뀌기 직전 위치로 돌아가 이어서 재생한다", () => {
    expect(resumeSeek("https://a/1", "https://a/2", playing, 0)).toEqual({ seek: 2760, resume: true });
  });

  it("클립이면 클립 시작을 빼고 상대 위치로 돌아간다. 일시정지였으면 멈춘 채 둔다", () => {
    expect(resumeSeek("https://a/1", "https://a/2", { abs: 130, paused: true }, 100)).toEqual({ seek: 30, resume: false });
  });

  it("첫 로드·같은 주소·재생한 적 없음이면 되돌릴 것이 없다", () => {
    expect(resumeSeek(undefined, "https://a/1", playing, 0)).toBeNull();
    expect(resumeSeek("https://a/1", "https://a/1", playing, 0)).toBeNull();
    expect(resumeSeek("https://a/1", "https://a/2", null, 0)).toBeNull();
  });
});

describe("mediaErrorAction — 재생 주소 만료", () => {
  it("네트워크·소스 오류는 주소를 한 번 새로 받는다", () => {
    expect(mediaErrorAction(2, 0)).toBe("refresh");
    expect(mediaErrorAction(4, 0)).toBe("refresh");
    expect(mediaErrorAction(undefined, 0)).toBe("refresh");
  });
  it("새 주소로도 실패하면 더 받지 않고 안내한다", () => {
    expect(mediaErrorAction(2, MEDIA_REFRESH_MAX)).toBe("fail");
    expect(mediaErrorAction(4, MEDIA_REFRESH_MAX)).toBe("fail");
  });
  it("디코딩 오류는 주소를 바꿔도 같아서 바로 안내한다", () => {
    expect(mediaErrorAction(3, 0)).toBe("fail");
  });
  it("사용자가 멈춘 중단은 오류로 다루지 않는다", () => {
    expect(mediaErrorAction(1, 0)).toBe("ignore");
  });
});
