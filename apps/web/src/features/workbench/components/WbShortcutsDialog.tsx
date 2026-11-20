import type { ReactNode } from "react";
import { Keyboard } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Modal } from "@/shared/ui/Modal";
import { cn } from "@/shared/lib/cn";
import type { ShortcutGroup } from "@/shared/ui/ShortcutsModal";
import { keysOf } from "../lib/shortcuts";
import { Keycaps } from "./Keycaps";

/**
 * 워크벤치 단축키 창을 그린다. 라벨링·검수 워크벤치와 완료 보기가 같이 쓴다.
 *
 * 두 열로 나눠 한 번에 다 보이게 한다. 보통 왼쪽이 재생, 오른쪽이 그 워크벤치의 편집·저장이다.
 * 재생 키만 있는 화면은 한 열이라 창 폭도 줄인다. 두 열 폭에 한 열만 두면 오른쪽 절반이 빈다.
 * 키는 글자 대신 키캡으로 그린다(`keysOf`). ⌘와 Ctrl의 관계는 창 아래 한 문장이 설명한다.
 * 공용 `ShortcutsModal`은 관리 화면 도움말이라 한 열 목록 그대로 둔다.
 */
export function WbShortcutsDialog({
  open,
  onClose,
  title,
  columns,
  footnote,
}: {
  open: boolean;
  onClose: () => void;
  /** 창 제목. `단축키 · 라벨링`처럼 워크벤치 이름을 붙인다. */
  title: string;
  /** 열별 그룹. 열은 둘까지 쓴다. */
  columns: ShortcutGroup[][];
  /** 창 아래 한 줄. 목록 밖의 규칙 하나만 쓴다. */
  footnote?: ReactNode;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      icon={<Keyboard size={16} aria-hidden="true" />}
      tone="neutral"
      size={columns.length > 1 ? "lg" : "md"}
      footer={
        <Button variant="secondary" onClick={onClose}>
          닫기
        </Button>
      }
    >
      <div className={cn("grid gap-x-8 gap-y-5", columns.length > 1 && "sm:grid-cols-2")}>
        {columns.map((groups, ci) => (
          <div key={ci} className="space-y-5">
            {groups.map((g) => (
              <section key={g.title}>
                <h3 className="mb-1.5 text-caption font-semibold text-label-3">{g.title}</h3>
                <dl>
                  {g.items.map((it) => (
                    <div key={it.keys + it.desc} className="flex items-center justify-between gap-3 py-1.5">
                      <dt className="text-footnote text-label">{it.desc}</dt>
                      <dd className="shrink-0">
                        <span className="sr-only">{it.keys}</span>
                        <Keycaps keys={keysOf(it.keys)} />
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
          </div>
        ))}
      </div>
      <p className="mt-5 text-footnote text-label-3">
        ⌘는 Windows에서 Ctrl입니다. 입력란에 쓰는 동안에는 단축키가 동작하지 않습니다.
      </p>
      {footnote && <p className="mt-1 text-footnote text-label-3">{footnote}</p>}
    </Modal>
  );
}
