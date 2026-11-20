/** 표 안의 작업자 셀. 멤버 표가 쓴다. */
import { Avatar } from "@/shared/ui/Avatar";

/**
 * 작업자 셀을 그린다. 아바타, 이름, 이메일(있을 때)을 보여 준다.
 * 파일 안에 사본을 만들지 않고 공용 `ui/Avatar`를 쓴다.
 */
export function WorkerCell({ name, email }: { name: string | null | undefined; email?: string | null }) {
  return (
    <div className="flex items-center gap-2">
      <Avatar name={name} size={28} />
      <div className="min-w-0">
        <div className="truncate font-medium text-label">{name ?? "—"}</div>
        {email !== undefined && <div className="truncate text-footnote text-label-3">{email ?? ""}</div>}
      </div>
    </div>
  );
}
