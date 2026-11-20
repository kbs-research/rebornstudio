/**
 * 단축키 도움말 모달. 앱 전체가 이 컴포넌트 하나를 쓴다. 그룹별로 키와 설명을 나열한다.
 *
 * 화면마다 표를 손으로 만들면 키 위치와 본문 크기가 달라져, 같은 도움말이 연 화면에 따라 다르게 보인다.
 * 새 화면은 이 컴포넌트에 그룹만 더한다.
 */
import type { ReactNode } from "react";

import { Button } from "./Button";
import { Modal } from "./Modal";

export interface ShortcutItem {
  keys: string;
  desc: string;
}
export interface ShortcutGroup {
  title: string;
  items: ShortcutItem[];
}

/** 단축키 그룹 목록을 모달로 보여 준다. 닫기 버튼을 푸터에 둔다. */
export function ShortcutsModal({
  open,
  onClose,
  groups,
  footnote,
}: {
  open: boolean;
  onClose: () => void;
  groups: ShortcutGroup[];
  /** 목록 아래 한 줄 안내. 목록에 없는 단축키를 어디서 찾는지처럼 목록 밖의 내용을 쓴다. */
  footnote?: ReactNode;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="단축키"
      /*
        닫기 버튼을 화면에 둔다. 헤더에 X가 없어서(Drawer와 다르다) 버튼이 없으면 Esc·백드롭을 아는 사람만 닫을 수 있다.
        이 도움말은 단축키를 모르는 사람이 연다.
      */
      footer={
        <Button variant="secondary" onClick={onClose}>
          닫기
        </Button>
      }
    >
      <div className="space-y-4">
        {groups.map((g) => (
          <section key={g.title}>
            <p className="mb-2 text-caption font-semibold text-label-3">{g.title}</p>
            <dl className="divide-y divide-separator overflow-hidden rounded-card border border-separator">
              {g.items.map((it) => (
                <div key={it.keys} className="flex items-center justify-between gap-3 px-4 py-2.5">
                  <dt className="text-callout text-label">{it.desc}</dt>
                  <dd>
                    <kbd className="rounded-mark border border-separator bg-canvas-2 px-1.5 py-0.5 font-mono text-micro tabular-nums text-label-2">
                      {it.keys}
                    </kbd>
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
        {footnote && <p className="text-footnote text-label-2">{footnote}</p>}
        {/* 여는 키(`?`)는 목록에 이미 있으므로 각주에는 목록에 없는 규칙 하나만 쓴다. */}
        <p className="text-footnote text-label-3">입력란에 입력하는 동안에는 단축키가 동작하지 않습니다.</p>
      </div>
    </Modal>
  );
}
