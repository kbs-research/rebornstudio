/** 단축키 표 검사. 처리기와 도움말이 같은 표를 읽는지, 도움말의 표기가 실제로 처리되는 키인지 확인한다. */
import { describe, expect, it } from "vitest";

import {
  EDIT_SHORTCUTS,
  helpColumns,
  hit,
  LABEL_HELP,
  LABEL_SHORTCUTS,
  legend,
  PLAYBACK_SHORTCUTS,
  spec,
  type ShortcutSpec,
} from "./shortcuts";
import { VIDEO_SHORTCUTS } from "./useVideoShortcuts";

/** 키 이벤트 대역. `match`가 보는 필드만 채운다. */
function key(k: string, mod: Partial<Record<"altKey" | "metaKey" | "ctrlKey" | "shiftKey", boolean>> = {}) {
  return { key: k, code: "", altKey: false, metaKey: false, ctrlKey: false, shiftKey: false, ...mod } as KeyboardEvent;
}

/** 도움말 표기의 키 이름을 `KeyboardEvent.key` 값으로 바꾼다. */
const KEY_NAME: Record<string, string> = { Space: " ", Esc: "Escape", Enter: "Enter", "−": "-" };

/**
 * 도움말 표기(`⌘/Ctrl + S`, `J / L`, `Esc`)를 그 표기대로 누른 키 이벤트 목록으로 바꾼다.
 * `⌘/Ctrl`은 ⌘와 Ctrl 두 경우를 모두 만든다. `/`로 나뉜 짝 키는 키마다 하나씩 만든다.
 */
function eventsOf(keys: string): KeyboardEvent[] {
  const parts = keys.split(/\s+\+\s+/);
  const cmd = parts.length > 1 && parts[0] === "⌘/Ctrl";
  const names = parts[parts.length - 1].split(/\s+\/\s+/).map((n) => KEY_NAME[n] ?? n.toLowerCase());
  return names.flatMap((n) => (cmd ? [key(n, { metaKey: true }), key(n, { ctrlKey: true })] : [key(n)]));
}

const ALL_TABLES: readonly (readonly ShortcutSpec[])[] = [PLAYBACK_SHORTCUTS, LABEL_SHORTCUTS, EDIT_SHORTCUTS];
const ALL_ROWS = ALL_TABLES.flat();

describe("표 한 곳", () => {
  it("재생 도움말 줄은 처리기가 읽는 표에서 나온다", () => {
    expect(VIDEO_SHORTCUTS).toEqual(legend(PLAYBACK_SHORTCUTS));
  });

  it("라벨링 단축키 창은 처리기가 읽는 표를 그대로 그린다", () => {
    expect(helpColumns(LABEL_HELP)).toEqual([
      [{ title: "재생", items: legend(PLAYBACK_SHORTCUTS) }],
      [
        { title: "문장 편집", items: legend(EDIT_SHORTCUTS) },
        { title: "저장 · 제출", items: legend(LABEL_SHORTCUTS) },
      ],
    ]);
  });

  it("도움말 줄에는 판정 함수가 섞이지 않는다", () => {
    for (const row of legend(PLAYBACK_SHORTCUTS)) {
      expect(Object.keys(row).sort()).toEqual(["desc", "keys"]);
    }
  });

  it("모든 줄에 표기와 설명이 있다", () => {
    for (const row of ALL_ROWS) {
      expect(row.keys.trim()).not.toBe("");
      expect(row.desc.trim()).not.toBe("");
    }
  });

  it("id는 표 안에서 겹치지 않는다", () => {
    for (const table of ALL_TABLES) {
      const ids = table.map((s) => s.id);
      expect(new Set(ids).size).toBe(ids.length);
    }
  });

  it("없는 id를 집으면 예외를 던진다", () => {
    expect(() => spec(PLAYBACK_SHORTCUTS, "없는키")).toThrow();
  });
});

describe("도움말 표기와 처리하는 키가 같다", () => {
  it("match가 없는 줄은 처리하는 위치가 적혀 있다", () => {
    for (const row of ALL_ROWS) {
      if (!row.match) expect(row.handledBy).toBeTruthy();
    }
  });

  it.each(ALL_ROWS.filter((r) => r.match).map((r) => [r.keys, r] as const))(
    "%s 표기대로 누르면 그 줄이 맞는다",
    (_keys, row) => {
      for (const e of eventsOf(row.keys)) expect(row.match!(e)).toBe(true);
    },
  );

  it("라벨링 단축키 창은 처리기가 있는 줄만 싣는다", () => {
    for (const groups of LABEL_HELP) {
      for (const g of groups) {
        for (const row of g.rows) expect(!!row.match || !!row.handledBy).toBe(true);
      }
    }
  });

  it("두지 않는 키는 어느 표에도 맞지 않는다", () => {
    const removed = [
      key("ArrowLeft", { altKey: true }),
      key("ArrowRight", { altKey: true }),
      key("ArrowUp"),
      key("ArrowDown"),
      key("Enter"),
      key("e"),
      key("g"),
      key("i"),
      key("o"),
      key("k", { metaKey: true }),
      key("k", { ctrlKey: true }),
    ];
    for (const e of removed) {
      for (const row of ALL_ROWS) expect(row.match?.(e) ?? false).toBe(false);
    }
  });
});

describe("재생 키", () => {
  it("맨손 J·L은 5초 이동, K는 일시정지", () => {
    expect(hit(PLAYBACK_SHORTCUTS, "back5", key("j"))).toBe(true);
    expect(hit(PLAYBACK_SHORTCUTS, "back5", key("L"))).toBe(true);
    expect(hit(PLAYBACK_SHORTCUTS, "pause", key("K"))).toBe(true);
  });

  it("수식키가 붙으면 재생 키가 아니다. ⌘S·⌘Enter 같은 화면 명령과 겹치지 않는다", () => {
    expect(hit(PLAYBACK_SHORTCUTS, "toggle", key(" "))).toBe(true);
    expect(hit(PLAYBACK_SHORTCUTS, "toggle", key(" ", { metaKey: true }))).toBe(false);
    expect(hit(PLAYBACK_SHORTCUTS, "back5", key("j", { altKey: true }))).toBe(false);
    expect(hit(PLAYBACK_SHORTCUTS, "rate1", key("0", { ctrlKey: true }))).toBe(false);
  });

  it("←/→는 VideoPlayer가 맡는다. 표에 적혀 있지만 여기서 맞지 않는다", () => {
    expect(hit(PLAYBACK_SHORTCUTS, "back10", key("ArrowLeft"))).toBe(false);
    expect(spec(PLAYBACK_SHORTCUTS, "back10").handledBy).toBe("VideoPlayer");
  });
});

describe("저장·제출과 편집 키", () => {
  it("⌘S와 Ctrl+S는 임시저장이다. Alt가 함께 눌리면 받지 않는다", () => {
    expect(hit(LABEL_SHORTCUTS, "save", key("s", { metaKey: true }))).toBe(true);
    expect(hit(LABEL_SHORTCUTS, "save", key("s", { ctrlKey: true }))).toBe(true);
    expect(hit(LABEL_SHORTCUTS, "save", key("s"))).toBe(false);
    expect(hit(LABEL_SHORTCUTS, "save", key("s", { metaKey: true, altKey: true }))).toBe(false);
  });

  it("한글 입력 상태의 ⌘S도 임시저장이다", () => {
    const e = { ...key("ㄴ", { metaKey: true }), code: "KeyS" } as KeyboardEvent;
    expect(hit(LABEL_SHORTCUTS, "save", e)).toBe(true);
  });

  it("맨손 Enter는 제출도 편집 확정도 아니다", () => {
    expect(hit(LABEL_SHORTCUTS, "submit", key("Enter"))).toBe(false);
    expect(hit(EDIT_SHORTCUTS, "confirm", key("Enter"))).toBe(false);
    expect(hit(LABEL_SHORTCUTS, "submit", key("Enter", { ctrlKey: true }))).toBe(true);
  });

  it("Esc는 편집 취소다", () => {
    expect(hit(EDIT_SHORTCUTS, "cancel", key("Escape"))).toBe(true);
  });
});
