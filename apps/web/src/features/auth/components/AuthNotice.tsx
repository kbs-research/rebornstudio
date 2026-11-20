/**
 * 인증 화면(로그인·재설정·최초 설정)의 안내 상자. 굵은 결과 한 줄과 작은 안내 한 줄로 나눈다.
 *
 * 네 화면이 같은 모양을 쓰게 하려고 둔다. 화면마다 상자를 손으로 그리면 색과 여백이 달라진다.
 * 오류는 `role="alert"`로 읽힌다. 나머지는 상태 안내라 `role="status"`다.
 */
import type { ReactNode } from "react";
import { CircleAlert, CircleCheck, Info, Link2 } from "lucide-react";

import { cn } from "@/shared/lib/cn";

type Tone = "ok" | "note" | "warn" | "bad";

const TONE: Record<Tone, { box: string; title: string; icon: typeof Info }> = {
  ok: { box: "bg-ok-muted", title: "text-ok-text", icon: CircleCheck },
  note: { box: "bg-note-muted", title: "text-note-text", icon: Link2 },
  warn: { box: "bg-warn-muted", title: "text-warn-text", icon: Info },
  bad: { box: "bg-bad-muted", title: "text-bad-text", icon: CircleAlert },
};

/** 안내 상자를 그린다. `hint`는 다음 할 일이나 조건을 적는 작은 줄이다. */
export function AuthNotice({ tone, title, hint }: { tone: Tone; title: ReactNode; hint?: ReactNode }) {
  const t = TONE[tone];
  const Icon = t.icon;
  return (
    <div role={tone === "bad" ? "alert" : "status"} className={cn("flex gap-2.5 rounded-control px-3.5 py-3", t.box)}>
      <Icon size={15} className={cn("mt-0.5 shrink-0", t.title)} aria-hidden="true" />
      <div className="min-w-0">
        <p className={cn("text-footnote font-semibold", t.title)}>{title}</p>
        {hint && <p className="mt-0.5 text-caption text-label-2">{hint}</p>}
      </div>
    </div>
  );
}

/** 인증 화면 폼 아래 두 링크 줄의 글자 모양 */
export const AUTH_LINK =
  "text-footnote font-medium text-label underline decoration-separator-strong underline-offset-4 hover:decoration-label";
